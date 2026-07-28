import AppKit
import Combine
import Foundation

@MainActor
final class ControllerModel: ObservableObject {
  static let shared = ControllerModel()

  @Published private(set) var phase: ControllerPhase = .checking
  @Published private(set) var snapshot: ServiceSnapshot = .stopped
  @Published var alertMessage: String?

  private(set) var allowImmediateTermination = false
  private let projectRoot: URL?
  private let webAppLauncher: WebAppLauncher
  private var monitorTask: Task<Void, Never>?
  private var actionTask: Task<Void, Never>?
  private var activeExecutor: ScriptExecutor?
  private var refreshInProgress = false
  private var terminationInProgress = false
  private var lastLoggedStatus: String?

  init(
    projectRoot: URL? = ProjectLocator.locate(),
    webAppLauncher: WebAppLauncher = WebAppLauncher()
  ) {
    self.projectRoot = projectRoot
    self.webAppLauncher = webAppLauncher
    if projectRoot == nil {
      phase = .ssdUnavailable
      snapshot.ssdAvailable = false
    }
  }

  var requiresStopConfirmation: Bool {
    TerminationPolicy.requiresConfirmation(phase: phase, snapshot: snapshot)
  }

  var canStart: Bool {
    !phase.isBusy && phase != .ssdUnavailable
  }

  var canStop: Bool {
    !phase.isBusy && (snapshot.anyServiceActive || snapshot.anyManagedProcess)
  }

  var canOpenWeb: Bool {
    !phase.isBusy && snapshot.webActive
  }

  func authorizeImmediateTermination() {
    allowImmediateTermination = true
  }

  func startMonitoring() {
    guard monitorTask == nil else { return }
    appendOperationalLog("controlador iniciado")
    monitorTask = Task { [weak self] in
      guard let self else { return }
      await refreshStatus()
      while !Task.isCancelled {
        try? await Task.sleep(nanoseconds: 4_000_000_000)
        if Task.isCancelled { break }
        await refreshStatus(preserveActionPhase: true)
      }
    }
  }

  func startRequested() {
    guard canStart, actionTask == nil else { return }
    phase = .starting
    alertMessage = nil
    appendOperationalLog("acción iniciar solicitada")
    actionTask = Task { [weak self] in
      await self?.performStart()
    }
  }

  func stopRequested() {
    guard canStop, actionTask == nil else { return }
    phase = .stopping
    alertMessage = nil
    appendOperationalLog("acción detener solicitada")
    actionTask = Task { [weak self] in
      await self?.performStop(showFailure: true)
    }
  }

  func openWeb() {
    guard canOpenWeb else { return }
    Task { [weak self] in
      await self?.launchWebApplication()
    }
  }

  func openLogs() {
    guard let projectRoot else {
      alertMessage = "No se puede localizar el proyecto porque el SSD no está disponible."
      return
    }
    let logs = projectRoot.appendingPathComponent("logs", isDirectory: true)
    do {
      try FileManager.default.createDirectory(
        at: logs,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
      NSWorkspace.shared.open(logs)
    } catch {
      alertMessage = "No se pudo abrir la carpeta de logs."
    }
  }

  func exitRequested() {
    guard !terminationInProgress else { return }
    Task { [weak self] in
      guard let self else { return }
      if await prepareForTermination() {
        authorizeImmediateTermination()
        NSApplication.shared.terminate(nil)
      }
    }
  }

  func prepareForTermination() async -> Bool {
    guard !terminationInProgress else { return false }
    terminationInProgress = true
    monitorTask?.cancel()
    monitorTask = nil

    if let actionTask {
      activeExecutor?.cancel()
      actionTask.cancel()
      await actionTask.value
      self.actionTask = nil
    }

    if snapshot.anyManagedProcess || snapshot.anyServiceActive || phase != .stopped {
      phase = .stopping
      await performStop(showFailure: true)
    } else {
      await refreshStatus()
    }

    let safeToExit = !snapshot.anyManagedProcess
    if !safeToExit {
      alertMessage = "No se pudieron detener todos los procesos gestionados. Revisa los logs."
      phase = .error
      startMonitoring()
    }
    terminationInProgress = false
    return safeToExit
  }

  func refreshStatus(preserveActionPhase: Bool = false) async {
    while refreshInProgress {
      try? await Task.sleep(nanoseconds: 100_000_000)
    }
    guard let projectRoot else {
      snapshot.ssdAvailable = false
      phase = .ssdUnavailable
      return
    }
    let statusScript = projectRoot.appendingPathComponent("scripts/status.sh")
    guard FileManager.default.isExecutableFile(atPath: statusScript.path) else {
      snapshot.ssdAvailable = false
      phase = .ssdUnavailable
      return
    }

    refreshInProgress = true
    let result = await ScriptExecutor().run(
      script: statusScript,
      arguments: ["--machine"],
      projectRoot: projectRoot,
      environment: ["DEUTSCHOS_LAUNCHER_NO_ALERT": "1"]
    )
    refreshInProgress = false

    do {
      snapshot = try StatusOutputParser.parse(result.standardOutput)
      if !preserveActionPhase || !phase.isBusy {
        phase = snapshot.derivedPhase
      }
      logStatusIfChanged()
    } catch {
      phase = .error
      alertMessage = "No se pudo interpretar el estado real de DeutschOS. Revisa los logs."
      appendOperationalLog("error al interpretar status.sh")
    }
  }

  private func performStart() async {
    guard let projectRoot else {
      phase = .ssdUnavailable
      actionTask = nil
      return
    }
    let executor = ScriptExecutor()
    activeExecutor = executor
    let result = await executor.run(
      script: projectRoot.appendingPathComponent("scripts/start.sh"),
      projectRoot: projectRoot,
      environment: [
        "DEUTSCHOS_APP_WRAPPER": "1",
        "DEUTSCHOS_LAUNCHER_NO_ALERT": "1",
      ]
    )
    activeExecutor = nil
    actionTask = nil
    await refreshStatus()

    guard !Task.isCancelled else { return }
    if !result.succeeded || !snapshot.allServicesActive {
      phase = .error
      alertMessage = UserFacingError.message(action: "el arranque", result: result)
      appendOperationalLog("arranque fallido (código \(result.exitCode))")
      return
    }
    appendOperationalLog("API y Web listas; abriendo aplicación web")
    await launchWebApplication()
  }

  private func launchWebApplication() async {
    do {
      switch try await webAppLauncher.launch() {
      case .activated:
        appendOperationalLog("aplicación web ya activa; traída al frente sin duplicarla")
      case .opened:
        appendOperationalLog(
          "aplicación web localizada y abierta mediante ~/Applications/DeutschOS.app"
        )
      case .safariFallback(let reason):
        appendOperationalLog("fallback a Safari: \(reason)")
        alertMessage = "Se abrió Safari como respaldo porque \(reason)."
      }
    } catch {
      appendOperationalLog("error de apertura: \(error.localizedDescription)")
      alertMessage = error.localizedDescription
    }
  }

  private func performStop(showFailure: Bool) async {
    guard let projectRoot else {
      phase = .ssdUnavailable
      actionTask = nil
      return
    }
    let executor = ScriptExecutor()
    activeExecutor = executor
    let result = await executor.run(
      script: projectRoot.appendingPathComponent("scripts/stop.sh"),
      projectRoot: projectRoot,
      environment: ["DEUTSCHOS_LAUNCHER_NO_ALERT": "1"]
    )
    activeExecutor = nil
    actionTask = nil
    await refreshStatus()

    if (!result.succeeded || snapshot.anyManagedProcess) && showFailure {
      phase = .error
      alertMessage = UserFacingError.message(action: "la detención", result: result)
      appendOperationalLog("detención fallida (código \(result.exitCode))")
    } else if snapshot.anyServiceActive && !snapshot.anyManagedProcess
      && showFailure && !terminationInProgress
    {
      alertMessage = "Los servicios activos son externos y se han preservado."
    }
  }

  private func logStatusIfChanged() {
    let signature = [
      phase.title,
      snapshot.lm_studioActive ? "lm_studio:on" : "lm_studio:off",
      snapshot.apiActive ? "api:on" : "api:off",
      snapshot.webActive ? "web:on" : "web:off",
      "pid:\(snapshot.lm_studioPID.rawValue),\(snapshot.apiPID.rawValue),\(snapshot.webPID.rawValue)",
    ].joined(separator: " ")
    guard signature != lastLoggedStatus else { return }
    lastLoggedStatus = signature
    appendOperationalLog("estado \(signature)")
  }

  private func appendOperationalLog(_ message: String) {
    guard let projectRoot else { return }
    let manager = FileManager.default
    let directory = projectRoot.appendingPathComponent("logs", isDirectory: true)
    let file = directory.appendingPathComponent("controller.log")
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
    guard let data = line.data(using: .utf8) else { return }
    do {
      try manager.createDirectory(
        at: directory,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700]
      )
      if !manager.fileExists(atPath: file.path) {
        try Data().write(to: file, options: .atomic)
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
      }
      let handle = try FileHandle(forWritingTo: file)
      try handle.seekToEnd()
      try handle.write(contentsOf: data)
      try handle.close()
    } catch {
      // Logging must never block control or shutdown actions.
    }
  }
}
