import Foundation

private final class MockWorkspace: WorkspaceOpening {
  var installedApplicationURL: URL?
  var applicationRunning = false
  var markRunningAfterOpen = true
  var shouldActivate = false
  var openApplicationError: Error?
  var shouldOpenURL = true
  private(set) var activatedBundleIdentifiers: [String] = []
  private(set) var openedApplications: [URL] = []
  private(set) var openedURLs: [URL] = []
  var stopOutcomes: [String: ApplicationStopOutcome] = [:]
  private(set) var stoppedBundleIdentifiers: [String] = []

  func applicationURL(bundleIdentifier: String) -> URL? {
    installedApplicationURL
  }

  func isApplicationRunning(bundleIdentifier: String) -> Bool {
    applicationRunning
  }

  func activateApplication(bundleIdentifier: String, at url: URL) async throws -> Bool {
    activatedBundleIdentifiers.append(bundleIdentifier)
    return shouldActivate
  }

  func openApplication(at url: URL) async throws {
    openedApplications.append(url)
    if let openApplicationError {
      throw openApplicationError
    }
    if markRunningAfterOpen {
      applicationRunning = true
    }
  }

  func openURL(_ url: URL) -> Bool {
    openedURLs.append(url)
    return shouldOpenURL
  }

  func stopApplications(bundleIdentifier: String, timeoutNanoseconds: UInt64) async
    -> ApplicationStopOutcome
  {
    stoppedBundleIdentifiers.append(bundleIdentifier)
    applicationRunning = false
    return stopOutcomes[bundleIdentifier] ?? .notRunning
  }
}

private struct MockFileChecker: FileChecking {
  var executablePaths: Set<String>

  func isExecutableFile(atPath path: String) -> Bool {
    executablePaths.contains(path)
  }
}

private final class MockProbe: LMStudioProbing {
  var responses: [Bool]
  var fallback: Bool
  private(set) var calls = 0

  init(_ responses: [Bool], fallback: Bool = false) {
    self.responses = responses
    self.fallback = fallback
  }

  func modelsEndpointIsReady() async -> Bool {
    calls += 1
    if !responses.isEmpty { return responses.removeFirst() }
    return fallback
  }
}

private struct MockCommandCall: Equatable {
  let executable: String
  let arguments: [String]
}

private final class MockCommandRunner: CommandRunning {
  var results: [ScriptResult]
  private(set) var calls: [MockCommandCall] = []
  private(set) var cancelled = false

  init(results: [ScriptResult] = [ScriptResult(exitCode: 0, standardOutput: "", standardError: "")]) {
    self.results = results
  }

  func run(
    executable: URL,
    arguments: [String],
    currentDirectory: URL,
    environment additions: [String: String]
  ) async -> ScriptResult {
    calls.append(MockCommandCall(executable: executable.path, arguments: arguments))
    if !results.isEmpty { return results.removeFirst() }
    return ScriptResult(exitCode: 0, standardOutput: "", standardError: "")
  }

  func cancel() {
    cancelled = true
  }
}

private struct ImmediateSleeper: Sleeping {
  func sleep(nanoseconds: UInt64) async throws {}
}

private enum MockOpenError: Error {
  case refused
}

enum TestFailure: LocalizedError {
  case expectation(String)

  var errorDescription: String? {
    switch self {
    case .expectation(let message): message
    }
  }
}

@main
struct ControllerTests {
  private static var passed = 0

  static func main() async throws {
    try runningManagedServices()
    try stoppedExternalFreeState()
    try stalePIDProducesPartialState()
    try unavailableSSDIsExplicit()
    try rejectsUnknownProtocol()
    try friendlyModelErrorDoesNotExposeTrace()
    try projectLocatorRequiresAllScripts()
    try terminationPolicyProtectsActiveServices()
    try await scriptExecutorRunsWithoutTerminal()
    try await webAppPathUsesCurrentHome()
    try await existingWebAppOpensWithoutSafari()
    try await runningWebAppActivatesWithoutDuplicate()
    try await missingWebAppFallsBackOnce()
    try await webAppOpenErrorFallsBackOnce()
    try await webAppStopTargetsExactBundle()
    try await lmStudioStopTargetsExactBundle()
    try await absentApplicationsStopIdempotently()
    try await lmStudioClosedOpensAndStartsServer()
    try await lmStudioRunningDoesNotOpenAgain()
    try await readyServerSkipsApplicationAndCLI()
    try await lmStudioCLIResolvesWithoutPATH()
    try await missingLMStudioApplicationIsClear()
    try await missingLMStudioCLIIsClear()
    try await lmStudioOpenFailureStopsFlow()
    try await lmStudioTimeoutDoesNotAcceptExitZero()
    try await lmStudioFailureCleansOnlyOwnedComponents()
    try controllerStartingPhasesAreBusyAndStoppable()
    if let integrationRoot = ProcessInfo.processInfo.environment[
      "DEUTSCHOS_CONTROLLER_INTEGRATION_ROOT"
    ] {
      try await realLifecycle(projectRoot: URL(fileURLWithPath: integrationRoot))
    }
    print("Controller Swift tests: \(passed) passed")
  }

  private static func expect(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    guard condition() else { throw TestFailure.expectation(message) }
  }

  private static func snapshot(
    ssd: String = "available",
    models: Int = 1,
    lm_studio: String,
    api: String,
    web: String,
    pidLMStudio: String,
    pidAPI: String,
    pidWeb: String,
    result: String
  ) throws -> ServiceSnapshot {
    try StatusOutputParser.parse(
      """
      format=deutschos-status-v1
      ssd=\(ssd)
      model_count=\(models)
      lm_studio=\(lm_studio)
      api=\(api)
      web=\(web)
      pid_lm_studio=\(pidLMStudio)
      pid_api=\(pidAPI)
      pid_web=\(pidWeb)
      result=\(result)
      """
    )
  }

  private static func runningManagedServices() throws {
    let value = try snapshot(
      lm_studio: "active",
      api: "active",
      web: "active",
      pidLMStudio: "managed",
      pidAPI: "managed",
      pidWeb: "managed",
      result: "running"
    )
    try expect(value.derivedPhase == .running, "running phase")
    try expect(value.allServicesActive, "all services active")
    try expect(value.anyManagedProcess, "managed ownership")
    passed += 1
  }

  private static func stoppedExternalFreeState() throws {
    let value = try snapshot(
      models: 0,
      lm_studio: "inactive",
      api: "inactive",
      web: "inactive",
      pidLMStudio: "absent",
      pidAPI: "absent",
      pidWeb: "absent",
      result: "stopped"
    )
    try expect(value.derivedPhase == .stopped, "stopped phase")
    try expect(!value.anyServiceActive, "no service active")
    try expect(!value.anyManagedProcess, "no managed process")
    passed += 1
  }

  private static func stalePIDProducesPartialState() throws {
    let value = try snapshot(
      lm_studio: "inactive",
      api: "inactive",
      web: "inactive",
      pidLMStudio: "stale",
      pidAPI: "absent",
      pidWeb: "absent",
      result: "partial"
    )
    try expect(value.derivedPhase == .partial, "stale PID must be partial")
    try expect(value.hasStalePID, "stale PID flag")
    passed += 1
  }

  private static func unavailableSSDIsExplicit() throws {
    let value = try snapshot(
      ssd: "unavailable",
      models: 0,
      lm_studio: "inactive",
      api: "inactive",
      web: "inactive",
      pidLMStudio: "absent",
      pidAPI: "absent",
      pidWeb: "absent",
      result: "stopped"
    )
    try expect(value.derivedPhase == .ssdUnavailable, "SSD phase")
    passed += 1
  }

  private static func rejectsUnknownProtocol() throws {
    do {
      _ = try StatusOutputParser.parse("format=unknown\n")
      throw TestFailure.expectation("unknown protocol accepted")
    } catch is StatusParseError {
      passed += 1
    }
  }

  private static func friendlyModelErrorDoesNotExposeTrace() throws {
    let result = ScriptResult(
      exitCode: 1,
      standardOutput: "",
      standardError: "ERROR: No hay modelos en /private/path"
    )
    let message = UserFacingError.message(action: "el arranque", result: result)
    try expect(message.contains("No se encontró ningún modelo"), "friendly model error")
    try expect(!message.contains("/private/path"), "private path exposed")
    passed += 1
  }

  private static func projectLocatorRequiresAllScripts() throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let scripts = root.appendingPathComponent("scripts")
    try manager.createDirectory(at: scripts, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: root) }

    for name in ["start.sh", "stop.sh", "status.sh"] {
      let path = scripts.appendingPathComponent(name)
      try "#!/bin/bash\n".write(to: path, atomically: true, encoding: .utf8)
      try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: path.path)
    }

    let appURL = root.appendingPathComponent("dist/DeutschOS.app")
    let located = ProjectLocator.locate(appURL: appURL, configuredRoot: root.path)
    try expect(located?.standardizedFileURL == root.standardizedFileURL, "project root")
    passed += 1
  }

  private static func terminationPolicyProtectsActiveServices() throws {
    var value = ServiceSnapshot.stopped
    try expect(
      !TerminationPolicy.requiresConfirmation(phase: .stopped, snapshot: value),
      "stopped state requested confirmation"
    )
    try expect(
      TerminationPolicy.requiresConfirmation(phase: .openingLMStudio, snapshot: value),
      "starting state did not request confirmation"
    )
    value.lm_studioActive = true
    try expect(
      TerminationPolicy.requiresConfirmation(phase: .running, snapshot: value),
      "active external service did not request confirmation"
    )
    passed += 1
  }

  private static func scriptExecutorRunsWithoutTerminal() async throws {
    let manager = FileManager.default
    let root = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let script = root.appendingPathComponent("probe.sh")
    try manager.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: root) }
    try "#!/bin/bash\nprintf 'controller-ok\\n'\n".write(
      to: script,
      atomically: true,
      encoding: .utf8
    )
    try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: script.path)

    let result = await ScriptExecutor().run(script: script, projectRoot: root)
    try expect(result.succeeded, "script executor failed")
    try expect(result.standardOutput == "controller-ok\n", "script output")
    passed += 1
  }

  private static func webAppPathUsesCurrentHome() async throws {
    let home = URL(fileURLWithPath: "/private/tmp/current-user-home", isDirectory: true)
    let launcher = WebAppLauncher(workspace: MockWorkspace(), homeDirectory: home)
    try expect(
      launcher.applicationURL.path == "/private/tmp/current-user-home/Applications/DeutschOS.app",
      "web app path does not derive from current home"
    )
    passed += 1
  }

  private static func existingWebAppOpensWithoutSafari() async throws {
    let manager = FileManager.default
    let home = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let app = home.appendingPathComponent("Applications/DeutschOS.app")
    try manager.createDirectory(at: app, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: home) }
    let workspace = MockWorkspace()
    let outcome = try await WebAppLauncher(
      workspace: workspace,
      fileManager: manager,
      homeDirectory: home
    ).launch()

    try expect(outcome == .opened, "existing web app was not opened")
    try expect(
      workspace.openedApplications.map(\.standardizedFileURL.path)
        == [app.standardizedFileURL.path],
      "wrong web app path opened"
    )
    try expect(workspace.openedURLs.isEmpty, "Safari opened alongside web app")
    passed += 1
  }

  private static func runningWebAppActivatesWithoutDuplicate() async throws {
    let workspace = MockWorkspace()
    workspace.shouldActivate = true
    let outcome = try await WebAppLauncher(workspace: workspace).launch()

    try expect(outcome == .activated, "running web app was not activated")
    try expect(
      workspace.activatedBundleIdentifiers == [WebAppLauncher.bundleIdentifier],
      "wrong bundle identifier activated"
    )
    try expect(workspace.openedApplications.isEmpty, "running web app duplicated")
    try expect(workspace.openedURLs.isEmpty, "Safari opened for running web app")
    passed += 1
  }

  private static func missingWebAppFallsBackOnce() async throws {
    let workspace = MockWorkspace()
    let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let outcome = try await WebAppLauncher(
      workspace: workspace,
      homeDirectory: home
    ).launch()

    guard case .safariFallback(let reason) = outcome else {
      throw TestFailure.expectation("missing web app did not use fallback")
    }
    try expect(reason.contains("no está instalada"), "missing-app reason not logged")
    try expect(workspace.openedURLs == [WebAppLauncher.webURL], "fallback was not unique")
    try expect(workspace.openedApplications.isEmpty, "missing web app was opened")
    passed += 1
  }

  private static func webAppOpenErrorFallsBackOnce() async throws {
    let manager = FileManager.default
    let home = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let app = home.appendingPathComponent("Applications/DeutschOS.app")
    try manager.createDirectory(at: app, withIntermediateDirectories: true)
    defer { try? manager.removeItem(at: home) }
    let workspace = MockWorkspace()
    workspace.openApplicationError = MockOpenError.refused
    let outcome = try await WebAppLauncher(
      workspace: workspace,
      fileManager: manager,
      homeDirectory: home
    ).launch()

    guard case .safariFallback(let reason) = outcome else {
      throw TestFailure.expectation("open error did not use fallback")
    }
    try expect(reason.contains("no pudo abrir"), "open-error reason not preserved")
    try expect(
      workspace.openedApplications.map(\.standardizedFileURL.path)
        == [app.standardizedFileURL.path],
      "web app open not attempted once"
    )
    try expect(workspace.openedURLs == [WebAppLauncher.webURL], "fallback was not unique")
    passed += 1
  }

  private static func webAppStopTargetsExactBundle() async throws {
    let workspace = MockWorkspace()
    workspace.stopOutcomes[WebAppLauncher.bundleIdentifier] = .terminated
    let outcome = await WebAppLauncher(workspace: workspace).stop()
    try expect(outcome == .terminated, "web app did not terminate normally")
    try expect(
      workspace.stoppedBundleIdentifiers == [WebAppLauncher.bundleIdentifier],
      "web app stop did not use the exact bundle identifier"
    )
    try expect(
      !workspace.stoppedBundleIdentifiers.contains("com.apple.Safari"),
      "Safari was targeted by web app stop"
    )
    passed += 1
  }

  private static func lmStudioStopTargetsExactBundle() async throws {
    let workspace = MockWorkspace()
    workspace.stopOutcomes[LMStudioApplication.bundleIdentifier] = .forceTerminated
    let outcome = await LMStudioApplication(workspace: workspace).stop()
    try expect(outcome == .forceTerminated, "LM Studio escalation not reported")
    try expect(
      workspace.stoppedBundleIdentifiers == [LMStudioApplication.bundleIdentifier],
      "LM Studio stop did not use the exact bundle identifier"
    )
    passed += 1
  }

  private static func absentApplicationsStopIdempotently() async throws {
    let workspace = MockWorkspace()
    let launcher = WebAppLauncher(workspace: workspace)
    let lmStudio = LMStudioApplication(workspace: workspace)
    let firstWeb = await launcher.stop()
    let secondWeb = await launcher.stop()
    let firstLMStudio = await lmStudio.stop()
    let secondLMStudio = await lmStudio.stop()
    try expect(firstWeb == .notRunning, "closed web app produced an error")
    try expect(secondWeb == .notRunning, "second web app stop was not idempotent")
    try expect(firstLMStudio == .notRunning, "closed LM Studio produced an error")
    try expect(secondLMStudio == .notRunning, "second LM Studio stop was not idempotent")
    passed += 1
  }

  private static func lmStudioClosedOpensAndStartsServer() async throws {
    let home = URL(fileURLWithPath: "/private/tmp/lm-home")
    let app = URL(fileURLWithPath: "/Applications/LM Studio.app")
    let cli = home.appendingPathComponent(".lmstudio/bin/lms")
    let workspace = MockWorkspace()
    workspace.installedApplicationURL = app
    let runner = MockCommandRunner()
    let probe = MockProbe([false, false, true])
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: [cli.path]),
      commandExecutor: runner,
      probe: probe,
      sleeper: ImmediateSleeper(),
      homeDirectory: home,
      environment: [:],
      applicationTimeoutNanoseconds: 2,
      serverTimeoutNanoseconds: 2,
      pollIntervalNanoseconds: 1
    )
    var stages: [LMStudioStartupStage] = []
    try await coordinator.ensureReady(
      stageChanged: { stages.append($0) },
      log: { _ in }
    )

    try expect(workspace.openedApplications == [app], "closed LM Studio was not opened")
    try expect(
      runner.calls.first?.arguments
        == ["server", "start", "--port", "1234", "--bind", "127.0.0.1"],
      "LM Studio server command was not exact"
    )
    try expect(
      stages == [.openingApplication, .startingServer, .waitingForServer],
      "LM Studio startup stages were out of order"
    )
    passed += 1
  }

  private static func lmStudioRunningDoesNotOpenAgain() async throws {
    let home = URL(fileURLWithPath: "/private/tmp/lm-running-home")
    let app = URL(fileURLWithPath: "/Applications/LM Studio.app")
    let cli = home.appendingPathComponent(".lmstudio/bin/lms")
    let workspace = MockWorkspace()
    workspace.installedApplicationURL = app
    workspace.applicationRunning = true
    let runner = MockCommandRunner()
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: [cli.path]),
      commandExecutor: runner,
      probe: MockProbe([false, false, true]),
      sleeper: ImmediateSleeper(),
      homeDirectory: home,
      environment: [:],
      serverTimeoutNanoseconds: 2,
      pollIntervalNanoseconds: 1
    )
    try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })

    try expect(workspace.openedApplications.isEmpty, "running LM Studio was opened again")
    try expect(runner.calls.count == 1, "inactive server was not started exactly once")
    passed += 1
  }

  private static func readyServerSkipsApplicationAndCLI() async throws {
    let workspace = MockWorkspace()
    let runner = MockCommandRunner()
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: []),
      commandExecutor: runner,
      probe: MockProbe([true]),
      sleeper: ImmediateSleeper(),
      environment: [:]
    )
    try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })
    try expect(workspace.openedApplications.isEmpty, "ready server opened LM Studio")
    try expect(runner.calls.isEmpty, "ready server ran lms server start")
    passed += 1
  }

  private static func lmStudioCLIResolvesWithoutPATH() async throws {
    let home = URL(fileURLWithPath: "/private/tmp/pathless-home")
    let app = URL(fileURLWithPath: "/Applications/LM Studio.app")
    let bundled = app.appendingPathComponent("Contents/Resources/app/.webpack/lms")
    let coordinator = LMStudioCoordinator(
      workspace: MockWorkspace(),
      fileChecker: MockFileChecker(executablePaths: [bundled.path]),
      commandExecutor: MockCommandRunner(),
      probe: MockProbe([]),
      sleeper: ImmediateSleeper(),
      homeDirectory: home,
      environment: ["PATH": "/usr/bin:/bin"]
    )
    let resolution = coordinator.resolveCLI(applicationURL: app)
    try expect(resolution?.url.path == bundled.path, "bundle CLI was not resolved explicitly")
    passed += 1
  }

  private static func missingLMStudioApplicationIsClear() async throws {
    let coordinator = LMStudioCoordinator(
      workspace: MockWorkspace(),
      fileChecker: MockFileChecker(executablePaths: []),
      commandExecutor: MockCommandRunner(),
      probe: MockProbe([false]),
      sleeper: ImmediateSleeper(),
      environment: [:]
    )
    do {
      try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })
      throw TestFailure.expectation("missing LM Studio application was accepted")
    } catch let error as LMStudioStartupError {
      try expect(error == .applicationMissing, "wrong missing-app error")
    }
    passed += 1
  }

  private static func missingLMStudioCLIIsClear() async throws {
    let workspace = MockWorkspace()
    workspace.installedApplicationURL = URL(fileURLWithPath: "/Applications/LM Studio.app")
    workspace.applicationRunning = true
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: []),
      commandExecutor: MockCommandRunner(),
      probe: MockProbe([false, false]),
      sleeper: ImmediateSleeper(),
      environment: [:]
    )
    do {
      try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })
      throw TestFailure.expectation("missing lms CLI was accepted")
    } catch let error as LMStudioStartupError {
      try expect(error == .cliMissing, "wrong missing-CLI error")
    }
    passed += 1
  }

  private static func lmStudioOpenFailureStopsFlow() async throws {
    let workspace = MockWorkspace()
    workspace.installedApplicationURL = URL(fileURLWithPath: "/Applications/LM Studio.app")
    workspace.openApplicationError = MockOpenError.refused
    let runner = MockCommandRunner()
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: []),
      commandExecutor: runner,
      probe: MockProbe([false]),
      sleeper: ImmediateSleeper(),
      environment: [:]
    )
    do {
      try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })
      throw TestFailure.expectation("LM Studio open failure was accepted")
    } catch let error as LMStudioStartupError {
      guard case .applicationOpenFailed = error else {
        throw TestFailure.expectation("wrong open-failure error")
      }
    }
    try expect(runner.calls.isEmpty, "CLI ran after app open failure")
    passed += 1
  }

  private static func lmStudioTimeoutDoesNotAcceptExitZero() async throws {
    let home = URL(fileURLWithPath: "/private/tmp/lm-timeout-home")
    let app = URL(fileURLWithPath: "/Applications/LM Studio.app")
    let cli = home.appendingPathComponent(".lmstudio/bin/lms")
    let workspace = MockWorkspace()
    workspace.installedApplicationURL = app
    workspace.applicationRunning = true
    let runner = MockCommandRunner(results: [
      ScriptResult(exitCode: 0, standardOutput: "ok", standardError: ""),
      ScriptResult(exitCode: 0, standardOutput: "", standardError: ""),
    ])
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: [cli.path]),
      commandExecutor: runner,
      probe: MockProbe([], fallback: false),
      sleeper: ImmediateSleeper(),
      homeDirectory: home,
      environment: [:],
      serverTimeoutNanoseconds: 2,
      pollIntervalNanoseconds: 1
    )
    do {
      try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })
      throw TestFailure.expectation("CLI exit zero was accepted without /v1/models")
    } catch let error as LMStudioStartupError {
      try expect(error == .serverTimedOut, "wrong server-timeout error")
    }
    try expect(
      runner.calls.map(\.arguments) == [
        ["server", "start", "--port", "1234", "--bind", "127.0.0.1"],
        ["server", "stop"],
      ],
      "timeout did not trigger focused server cleanup"
    )
    passed += 1
  }

  private static func lmStudioFailureCleansOnlyOwnedComponents() async throws {
    let home = URL(fileURLWithPath: "/private/tmp/lm-cleanup-home")
    let app = URL(fileURLWithPath: "/Applications/LM Studio.app")
    let cli = home.appendingPathComponent(".lmstudio/bin/lms")
    let workspace = MockWorkspace()
    workspace.installedApplicationURL = app
    workspace.stopOutcomes[LMStudioCoordinator.bundleIdentifier] = .terminated
    let runner = MockCommandRunner(results: [
      ScriptResult(exitCode: 0, standardOutput: "", standardError: ""),
      ScriptResult(exitCode: 0, standardOutput: "", standardError: ""),
    ])
    let coordinator = LMStudioCoordinator(
      workspace: workspace,
      fileChecker: MockFileChecker(executablePaths: [cli.path]),
      commandExecutor: runner,
      probe: MockProbe([], fallback: false),
      sleeper: ImmediateSleeper(),
      homeDirectory: home,
      environment: [:],
      serverTimeoutNanoseconds: 1,
      pollIntervalNanoseconds: 1
    )
    do {
      try await coordinator.ensureReady(stageChanged: { _ in }, log: { _ in })
    } catch {}
    try expect(
      workspace.stoppedBundleIdentifiers == [LMStudioCoordinator.bundleIdentifier],
      "app opened by failed run was not cleaned exactly"
    )
    passed += 1
  }

  private static func controllerStartingPhasesAreBusyAndStoppable() throws {
    for phase in [
      ControllerPhase.openingLMStudio,
      .startingLMStudioServer,
      .waitingLMStudio,
      .startingAPI,
      .startingWeb,
      .openingWeb,
    ] {
      try expect(phase.isBusy, "\(phase.title) was not busy")
      try expect(phase.isStarting, "\(phase.title) was not a startup phase")
    }
    passed += 1
  }

  private static func realLifecycle(projectRoot: URL) async throws {
    let scripts = projectRoot.appendingPathComponent("scripts")
    let executor = ScriptExecutor()
    _ = await executor.run(
      script: scripts.appendingPathComponent("stop.sh"),
      projectRoot: projectRoot
    )
    let controller = await MainActor.run { ControllerModel(projectRoot: projectRoot) }
    await controller.refreshStatus()
    let runDirectory = projectRoot.appendingPathComponent("run")
    let pidFiles = ["lm_studio.pid", "api.pid", "web.pid"].map {
      runDirectory.appendingPathComponent($0)
    }

    await MainActor.run {
      controller.startRequested()
      controller.startRequested()
    }
    try await wait(controller: controller, for: .running, timeout: 70)
    let before = try pidFiles.map { try String(contentsOf: $0, encoding: .utf8) }
    await MainActor.run { controller.startRequested() }
    try? await Task.sleep(nanoseconds: 500_000_000)
    let after = try pidFiles.map { try String(contentsOf: $0, encoding: .utf8) }
    try expect(before == after, "second start changed managed PIDs")

    await MainActor.run { controller.stopRequested() }
    try await wait(controller: controller, for: .stopped, timeout: 30)

    await MainActor.run { controller.startRequested() }
    try await wait(controller: controller, for: .running, timeout: 70)
    let safeToExit = await controller.prepareForTermination()
    try expect(safeToExit, "Salir did not reach a safe state")
    await controller.refreshStatus()
    let finalPhase = await MainActor.run { controller.phase }
    let managed = await MainActor.run { controller.snapshot.anyManagedProcess }
    try expect(finalPhase == .stopped, "real services remained active")
    try expect(!managed, "managed PID remained after Salir")
    passed += 1
  }

  private static func wait(
    controller: ControllerModel,
    for expected: ControllerPhase,
    timeout: Int
  ) async throws {
    for _ in 0..<timeout * 4 {
      let state = await MainActor.run { (controller.phase, controller.alertMessage) }
      if state.0 == expected { return }
      if state.0 == .error {
        throw TestFailure.expectation(state.1 ?? "controller entered error state")
      }
      try? await Task.sleep(nanoseconds: 250_000_000)
    }
    throw TestFailure.expectation("timeout waiting for \(expected.title)")
  }
}
