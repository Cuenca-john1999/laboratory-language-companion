import Foundation

protocol FileChecking {
  func isExecutableFile(atPath path: String) -> Bool
}

struct SystemFileChecker: FileChecking {
  func isExecutableFile(atPath path: String) -> Bool {
    FileManager.default.isExecutableFile(atPath: path)
  }
}

protocol Sleeping {
  func sleep(nanoseconds: UInt64) async throws
}

struct SystemSleeper: Sleeping {
  func sleep(nanoseconds: UInt64) async throws {
    try await Task.sleep(nanoseconds: nanoseconds)
  }
}

protocol LMStudioProbing {
  func modelsEndpointIsReady() async -> Bool
}

struct URLSessionLMStudioProbe: LMStudioProbing {
  let endpoint: URL

  init(endpoint: URL = URL(string: "http://127.0.0.1:1234/v1/models")!) {
    self.endpoint = endpoint
  }

  func modelsEndpointIsReady() async -> Bool {
    var request = URLRequest(url: endpoint)
    request.timeoutInterval = 3
    request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    do {
      let (data, response) = try await URLSession.shared.data(for: request)
      guard
        let http = response as? HTTPURLResponse,
        (200..<300).contains(http.statusCode),
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
        let models = object["data"] as? [[String: Any]]
      else {
        return false
      }
      return models.allSatisfy { model in
        guard let identifier = model["id"] as? String else { return false }
        return !identifier.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      }
    } catch {
      return false
    }
  }
}

enum LMStudioStartupStage: Equatable {
  case openingApplication
  case startingServer
  case waitingForServer
}

enum LMStudioStartupError: LocalizedError, Equatable {
  case applicationMissing
  case applicationOpenFailed(String)
  case applicationLaunchTimedOut
  case cliMissing
  case cliFailed(Int32)
  case serverTimedOut

  var errorDescription: String? {
    switch self {
    case .applicationMissing:
      "No se encontró LM Studio en este Mac."
    case .applicationOpenFailed:
      "macOS no pudo abrir LM Studio."
    case .applicationLaunchTimedOut:
      "LM Studio no terminó de abrirse antes del tiempo límite."
    case .cliMissing:
      "No se encontró el comando local de LM Studio."
    case .cliFailed:
      "LM Studio no pudo iniciar su servidor local."
    case .serverTimedOut:
      "LM Studio no pudo iniciar su servidor local."
    }
  }
}

struct LMStudioCLIResolution: Equatable {
  let url: URL
  let displayPath: String
}

final class LMStudioCoordinator {
  static let bundleIdentifier = "ai.elementlabs.lmstudio"

  private let workspace: WorkspaceOpening
  private let fileChecker: FileChecking
  private let commandExecutor: CommandRunning
  private let probe: LMStudioProbing
  private let sleeper: Sleeping
  private let homeDirectory: URL
  private let environment: [String: String]
  private let applicationTimeoutNanoseconds: UInt64
  private let serverTimeoutNanoseconds: UInt64
  private let pollIntervalNanoseconds: UInt64

  init(
    workspace: WorkspaceOpening = SystemWorkspace(),
    fileChecker: FileChecking = SystemFileChecker(),
    commandExecutor: CommandRunning = ScriptExecutor(),
    probe: LMStudioProbing = URLSessionLMStudioProbe(),
    sleeper: Sleeping = SystemSleeper(),
    homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
    environment: [String: String] = ProcessInfo.processInfo.environment,
    applicationTimeoutNanoseconds: UInt64 = 20_000_000_000,
    serverTimeoutNanoseconds: UInt64 = 60_000_000_000,
    pollIntervalNanoseconds: UInt64 = 500_000_000
  ) {
    self.workspace = workspace
    self.fileChecker = fileChecker
    self.commandExecutor = commandExecutor
    self.probe = probe
    self.sleeper = sleeper
    self.homeDirectory = homeDirectory
    self.environment = environment
    self.applicationTimeoutNanoseconds = applicationTimeoutNanoseconds
    self.serverTimeoutNanoseconds = serverTimeoutNanoseconds
    self.pollIntervalNanoseconds = pollIntervalNanoseconds
  }

  func resolveCLI(applicationURL: URL?) -> LMStudioCLIResolution? {
    let configured =
      environment["LLC_LAUNCHER_LMS_BIN"]
      ?? environment["LLC_LAUNCHER_LM_STUDIO_BIN"]
      ?? environment["DEUTSCHOS_LAUNCHER_LMS_BIN"]
      ?? environment["DEUTSCHOS_LAUNCHER_LM_STUDIO_BIN"]
    var candidates: [(URL, String)] = []
    if let configured, !configured.isEmpty {
      let url = URL(fileURLWithPath: configured)
      candidates.append((url, sanitizedPath(url)))
    }
    let canonical = homeDirectory.appendingPathComponent(".lmstudio/bin/lms")
    candidates.append((canonical, "~/.lmstudio/bin/lms"))
    if let applicationURL {
      candidates.append(
        (
          applicationURL.appendingPathComponent("Contents/Resources/app/.webpack/lms"),
          "\(applicationURL.path)/Contents/Resources/app/.webpack/lms"
        )
      )
    }
    candidates.append(
      (homeDirectory.appendingPathComponent(".local/bin/lms"), "~/.local/bin/lms")
    )

    var seen = Set<String>()
    for (candidate, displayPath) in candidates where seen.insert(candidate.path).inserted {
      if fileChecker.isExecutableFile(atPath: candidate.path) {
        return LMStudioCLIResolution(url: candidate, displayPath: displayPath)
      }
    }
    return nil
  }

  func ensureReady(
    stageChanged: @escaping @MainActor (LMStudioStartupStage) -> Void,
    log: @escaping @MainActor (String) -> Void
  ) async throws {
    if await probe.modelsEndpointIsReady() {
      await log("servidor de LM Studio ya disponible; se reutiliza")
      return
    }

    guard
      let applicationURL = workspace.applicationURL(
        bundleIdentifier: Self.bundleIdentifier
      )
    else {
      await log("LM Studio no encontrada mediante el bundle \(Self.bundleIdentifier)")
      throw LMStudioStartupError.applicationMissing
    }

    var openedApplication = false
    var startedServer = false
    do {
      if workspace.isApplicationRunning(bundleIdentifier: Self.bundleIdentifier) {
        await log("LM Studio.app ya estaba abierta; no se crea otra instancia")
      } else {
        await stageChanged(.openingApplication)
        await log("abriendo LM Studio.app por bundle exacto")
        do {
          try await workspace.openApplication(at: applicationURL)
        } catch {
          await log("macOS rechazó la apertura de LM Studio: \(error.localizedDescription)")
          throw LMStudioStartupError.applicationOpenFailed(error.localizedDescription)
        }
        openedApplication = true
        try await waitForApplication()
        await log("LM Studio.app abierta correctamente")
      }

      if await probe.modelsEndpointIsReady() {
        await log("servidor de LM Studio ya disponible tras abrir la aplicación")
        return
      }

      guard let cli = resolveCLI(applicationURL: applicationURL) else {
        await log("no se encontró un CLI lms ejecutable en las rutas admitidas")
        throw LMStudioStartupError.cliMissing
      }
      await log("CLI de LM Studio resuelto: \(cli.displayPath)")
      await stageChanged(.startingServer)
      await log("ejecutando lms server start en 127.0.0.1:1234")
      let result = await commandExecutor.run(
        executable: cli.url,
        arguments: ["server", "start", "--port", "1234", "--bind", "127.0.0.1"],
        currentDirectory: applicationURL.deletingLastPathComponent(),
        environment: [:]
      )
      await log("lms server start finalizó con código \(result.exitCode)")
      let detail = conciseOutput(result)
      if !detail.isEmpty { await log("salida de lms: \(detail)") }
      guard result.succeeded else {
        throw LMStudioStartupError.cliFailed(result.exitCode)
      }
      startedServer = true
      await stageChanged(.waitingForServer)
      await log("esperando respuesta válida de /v1/models")
      try await waitForServer()
      await log("servidor de LM Studio disponible en /v1/models")
    } catch {
      await cleanupAfterFailure(
        applicationURL: applicationURL,
        openedApplication: openedApplication,
        startedServer: startedServer,
        log: log
      )
      throw error
    }
  }

  func cancel() {
    commandExecutor.cancel()
  }

  private func waitForApplication() async throws {
    var waited: UInt64 = 0
    while waited < applicationTimeoutNanoseconds {
      try Task.checkCancellation()
      if workspace.isApplicationRunning(bundleIdentifier: Self.bundleIdentifier) {
        try await sleeper.sleep(nanoseconds: pollIntervalNanoseconds)
        try Task.checkCancellation()
        if workspace.isApplicationRunning(bundleIdentifier: Self.bundleIdentifier) {
          return
        }
      }
      try await sleeper.sleep(nanoseconds: pollIntervalNanoseconds)
      waited += pollIntervalNanoseconds
    }
    throw LMStudioStartupError.applicationLaunchTimedOut
  }

  private func waitForServer() async throws {
    var waited: UInt64 = 0
    while waited < serverTimeoutNanoseconds {
      try Task.checkCancellation()
      if await probe.modelsEndpointIsReady() { return }
      try await sleeper.sleep(nanoseconds: pollIntervalNanoseconds)
      waited += pollIntervalNanoseconds
    }
    throw LMStudioStartupError.serverTimedOut
  }

  private func cleanupAfterFailure(
    applicationURL: URL,
    openedApplication: Bool,
    startedServer: Bool,
    log: @escaping @MainActor (String) -> Void
  ) async {
    await log("limpieza focalizada tras fallo o cancelación de LM Studio")
    if startedServer, let cli = resolveCLI(applicationURL: applicationURL) {
      let result = await commandExecutor.run(
        executable: cli.url,
        arguments: ["server", "stop"],
        currentDirectory: applicationURL.deletingLastPathComponent(),
        environment: [:]
      )
      await log("limpieza del servidor LM Studio finalizó con código \(result.exitCode)")
    }
    if openedApplication {
      _ = await workspace.stopApplications(
        bundleIdentifier: Self.bundleIdentifier,
        timeoutNanoseconds: 5_000_000_000
      )
      await log("LM Studio.app abierta por este arranque se cerró durante la limpieza")
    } else {
      await log("LM Studio.app preexistente se conservó")
    }
  }

  private func sanitizedPath(_ url: URL) -> String {
    let home = homeDirectory.standardizedFileURL.path
    let path = url.standardizedFileURL.path
    if path == home { return "~" }
    if path.hasPrefix(home + "/") {
      return "~/" + path.dropFirst(home.count + 1)
    }
    return path
  }

  private func conciseOutput(_ result: ScriptResult) -> String {
    (result.standardError + "\n" + result.standardOutput)
      .split(whereSeparator: \.isNewline)
      .prefix(3)
      .joined(separator: " | ")
  }
}
