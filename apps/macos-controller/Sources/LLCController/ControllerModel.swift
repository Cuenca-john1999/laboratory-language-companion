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
  private let lmStudioApplication: LMStudioApplication
  private let lmStudioCoordinator: LMStudioCoordinator
  private var monitorTask: Task<Void, Never>?
  private var actionTask: Task<Void, Never>?
  private var activeExecutor: ScriptExecutor?
  private var refreshInProgress = false
  private var terminationInProgress = false
  private var lastLoggedStatus: String?
  private var actionIdentifier = UUID()

  init(
    projectRoot: URL? = ProjectLocator.locate(),
    webAppLauncher: WebAppLauncher = WebAppLauncher(),
    lmStudioApplication: LMStudioApplication = LMStudioApplication(),
    lmStudioCoordinator: LMStudioCoordinator = LMStudioCoordinator()
  ) {
    self.projectRoot = projectRoot
    self.webAppLauncher = webAppLauncher
    self.lmStudioApplication = lmStudioApplication
    self.lmStudioCoordinator = lmStudioCoordinator
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
    phase != .checking && phase != .stopping
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
    phase = .openingLMStudio
    alertMessage = nil
    appendOperationalLog("acción iniciar solicitada")
    let identifier = UUID()
    actionIdentifier = identifier
    actionTask = Task { [weak self] in
      guard let self else { return }
      await performStart()
      if actionIdentifier == identifier {
        actionTask = nil
      }
    }
  }

  func stopRequested() {
    guard canStop else { return }
    if let currentAction = actionTask, phase.isStarting {
      appendOperationalLog("cancelación solicitada durante el arranque")
      phase = .stopping
      alertMessage = nil
      lmStudioCoordinator.cancel()
      activeExecutor?.cancel()
      currentAction.cancel()
      let identifier = UUID()
      actionIdentifier = identifier
      actionTask = Task { [weak self] in
        guard let self else { return }
        await currentAction.value
        appendOperationalLog("flujo de arranque cancelado; iniciando limpieza completa")
        await performStop(showFailure: true)
        if actionIdentifier == identifier {
          actionTask = nil
        }
      }
      return
    }
    guard actionTask == nil else { return }
    phase = .stopping
    alertMessage = nil
    appendOperationalLog("acción detener solicitada")
    let identifier = UUID()
    actionIdentifier = identifier
    actionTask = Task { [weak self] in
      guard let self else { return }
      await performStop(showFailure: true)
      if actionIdentifier == identifier {
        actionTask = nil
      }
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
      actionIdentifier = UUID()
      lmStudioCoordinator.cancel()
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

    let safeToExit = !snapshot.anyManagedProcess && !snapshot.anyServiceActive
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
      environment: ["LLC_LAUNCHER_NO_ALERT": "1"]
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
      alertMessage = "No se pudo interpretar el estado real de LLC. Revisa los logs."
      appendOperationalLog("error al interpretar status.sh")
    }
  }

  private func performStart() async {
    guard let projectRoot else {
      phase = .ssdUnavailable
      return
    }
    do {
      try await lmStudioCoordinator.ensureReady(
        stageChanged: { [weak self] stage in
          guard let self else { return }
          switch stage {
          case .openingApplication:
            phase = .openingLMStudio
          case .startingServer:
            phase = .startingLMStudioServer
          case .waitingForServer:
            phase = .waitingLMStudio
          }
        },
        log: { [weak self] message in
          self?.appendOperationalLog(message)
        }
      )
    } catch is CancellationError {
      appendOperationalLog("arranque cancelado durante la preparación de LM Studio")
      return
    } catch {
      guard !Task.isCancelled else {
        appendOperationalLog("arranque cancelado durante la preparación de LM Studio")
        return
      }
      phase = .error
      alertMessage = error.localizedDescription + " Revisa los logs para obtener más información."
      appendOperationalLog("arranque detenido en LM Studio: \(error.localizedDescription)")
      return
    }
    guard !Task.isCancelled else { return }

    phase = .startingAPI
    let executor = ScriptExecutor()
    activeExecutor = executor
    let progressTask = Task { [weak self] in
      guard let self else { return }
      while activeExecutor === executor && !Task.isCancelled {
        await refreshStatus(preserveActionPhase: true)
        guard phase != .stopping else { continue }
        if snapshot.apiActive && !snapshot.webActive {
          phase = .startingWeb
        } else if !snapshot.apiActive {
          phase = .startingAPI
        }
        try? await Task.sleep(nanoseconds: 250_000_000)
      }
    }
    let result = await executor.run(
      script: projectRoot.appendingPathComponent("scripts/start.sh"),
      projectRoot: projectRoot,
      environment: [
        "LLC_APP_WRAPPER": "1",
        "LLC_LAUNCHER_NO_ALERT": "1",
        "LLC_LM_STUDIO_READY": "1",
      ]
    )
    activeExecutor = nil
    await progressTask.value
    guard !Task.isCancelled else { return }
    await refreshStatus()

    if !result.succeeded || !snapshot.allServicesActive {
      phase = .error
      alertMessage = UserFacingError.message(action: "el arranque", result: result)
      appendOperationalLog("arranque fallido (código \(result.exitCode))")
      return
    }
    phase = .openingWeb
    appendOperationalLog("LM Studio, API y Web listas; abriendo aplicación web")
    await launchWebApplication()
    guard alertMessage == nil else {
      phase = .error
      return
    }
    phase = .running
    appendOperationalLog("arranque completo; LLC activo")
  }

  private func launchWebApplication() async {
    do {
      switch try await webAppLauncher.launch() {
      case .activated:
        appendOperationalLog("aplicación web ya activa; traída al frente sin duplicarla")
      case .opened:
        appendOperationalLog("aplicación web instalada localizada y abierta")
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
      return
    }
    appendOperationalLog("inicio del apagado completo")
    var nativeFailures: [String] = []
    switch await webAppLauncher.stop() {
    case .notRunning:
      appendOperationalLog("aplicación web no estaba ejecutándose")
    case .terminated:
      appendOperationalLog("aplicación web cerrada normalmente")
    case .forceTerminated:
      appendOperationalLog("aplicación web cerrada con forceTerminate sobre el bundle exacto")
    case .timedOut(let pids):
      appendOperationalLog("timeout al cerrar aplicación web; PID \(pids)")
      nativeFailures.append("aplicación web")
    }

    let executor = ScriptExecutor()
    activeExecutor = executor
    let result = await executor.run(
      script: projectRoot.appendingPathComponent("scripts/stop.sh"),
      projectRoot: projectRoot,
      environment: [
        "LLC_LAUNCHER_NO_ALERT": "1",
        "LLC_LM_APP_WILL_CLOSE": "1",
      ]
    )
    activeExecutor = nil

    switch await lmStudioApplication.stop() {
    case .notRunning:
      appendOperationalLog("LM Studio.app no estaba ejecutándose")
    case .terminated:
      appendOperationalLog("LM Studio.app cerrada normalmente")
    case .forceTerminated:
      appendOperationalLog("LM Studio.app cerrada con forceTerminate sobre el bundle exacto")
    case .timedOut(let pids):
      appendOperationalLog("timeout al cerrar LM Studio.app; PID \(pids)")
      nativeFailures.append("LM Studio.app")
    }

    let verificationResult = await executor.run(
      script: projectRoot.appendingPathComponent("scripts/stop.sh"),
      projectRoot: projectRoot,
      environment: ["LLC_LAUNCHER_NO_ALERT": "1"]
    )
    await refreshStatus()

    if (!result.succeeded || !verificationResult.succeeded || !nativeFailures.isEmpty
      || snapshot.anyManagedProcess
      || snapshot.anyServiceActive)
      && showFailure
    {
      phase = .error
      let active = [
        nativeFailures.isEmpty ? nil : nativeFailures.joined(separator: ", "),
        snapshot.lm_studioActive ? "LM Studio/puerto 1234" : nil,
        snapshot.apiActive ? "FastAPI/puerto 8000" : nil,
        snapshot.webActive ? "Next.js/puerto 3000" : nil,
      ].compactMap { $0 }.joined(separator: ", ")
      alertMessage = active.isEmpty
        ? UserFacingError.message(action: "la detención", result: result)
        : "Detención incompleta: \(active). Revisa los logs."
      appendOperationalLog(
        "apagado parcial (código \(result.exitCode)); componentes activos: \(active)"
      )
    } else {
      phase = .stopped
      appendOperationalLog("apagado completo")
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
