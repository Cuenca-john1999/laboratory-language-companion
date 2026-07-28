import AppKit
import Foundation

enum WebAppLaunchOutcome: Equatable {
  case activated
  case opened
  case safariFallback(reason: String)
}

enum ApplicationStopOutcome: Equatable {
  case notRunning
  case terminated
  case forceTerminated
  case timedOut(processIdentifiers: [pid_t])
}

protocol WorkspaceOpening {
  func applicationURL(bundleIdentifier: String) -> URL?
  func isApplicationRunning(bundleIdentifier: String) -> Bool
  func activateApplication(bundleIdentifier: String, at url: URL) async throws -> Bool
  func openApplication(at url: URL) async throws
  func openURL(_ url: URL) -> Bool
  func stopApplications(bundleIdentifier: String, timeoutNanoseconds: UInt64) async
    -> ApplicationStopOutcome
}

struct SystemWorkspace: WorkspaceOpening {
  func applicationURL(bundleIdentifier: String) -> URL? {
    NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier)
  }

  func isApplicationRunning(bundleIdentifier: String) -> Bool {
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
      .contains { !$0.isTerminated }
  }

  func activateApplication(bundleIdentifier: String, at url: URL) async throws -> Bool {
    guard
      let application = NSRunningApplication.runningApplications(
        withBundleIdentifier: bundleIdentifier
      ).first
    else {
      return false
    }
    if !application.activate(options: [.activateAllWindows, .activateIgnoringOtherApps]) {
      // Launch Services reuses the running instance and reliably raises Safari
      // web apps that reject NSRunningApplication's direct activation request.
      try await openApplication(at: url)
    }
    return true
  }

  func openApplication(at url: URL) async throws {
    try await withCheckedThrowingContinuation {
      (continuation: CheckedContinuation<Void, Error>) in
      NSWorkspace.shared.openApplication(
        at: url,
        configuration: NSWorkspace.OpenConfiguration()
      ) { _, error in
        if let error {
          continuation.resume(throwing: error)
        } else {
          continuation.resume(returning: ())
        }
      }
    }
  }

  func openURL(_ url: URL) -> Bool {
    NSWorkspace.shared.open(url)
  }

  func stopApplications(bundleIdentifier: String, timeoutNanoseconds: UInt64) async
    -> ApplicationStopOutcome
  {
    let applications = NSRunningApplication.runningApplications(
      withBundleIdentifier: bundleIdentifier
    )
    guard !applications.isEmpty else { return .notRunning }

    applications.forEach { _ = $0.terminate() }
    if await waitUntilStopped(applications, timeoutNanoseconds: timeoutNanoseconds) {
      return .terminated
    }

    let remaining = applications.filter(\.isTerminated.not)
    remaining.forEach { _ = $0.forceTerminate() }
    if await waitUntilStopped(remaining, timeoutNanoseconds: timeoutNanoseconds) {
      return .forceTerminated
    }
    return .timedOut(
      processIdentifiers: remaining.filter(\.isTerminated.not).map(\.processIdentifier)
    )
  }

  private func waitUntilStopped(
    _ applications: [NSRunningApplication],
    timeoutNanoseconds: UInt64
  ) async -> Bool {
    let interval: UInt64 = 100_000_000
    var waited: UInt64 = 0
    while applications.contains(where: { !$0.isTerminated }) && waited < timeoutNanoseconds {
      try? await Task.sleep(nanoseconds: interval)
      waited += interval
    }
    return applications.allSatisfy(\.isTerminated)
  }
}

private extension Bool {
  var not: Bool { !self }
}

struct WebAppLauncher {
  static let bundleIdentifier =
    "com.apple.Safari.WebApp.BCBE6F7E-0A76-48D7-A483-B854E8E07820"
  static let webURL = URL(string: "http://127.0.0.1:3000")!

  let workspace: WorkspaceOpening
  let fileManager: FileManager
  let homeDirectory: URL

  init(
    workspace: WorkspaceOpening = SystemWorkspace(),
    fileManager: FileManager = .default,
    homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser
  ) {
    self.workspace = workspace
    self.fileManager = fileManager
    self.homeDirectory = homeDirectory
  }

  var applicationURL: URL {
    homeDirectory
      .appendingPathComponent("Applications", isDirectory: true)
      .appendingPathComponent("DeutschOS.app", isDirectory: true)
  }

  func launch() async throws -> WebAppLaunchOutcome {
    if try await workspace.activateApplication(
      bundleIdentifier: Self.bundleIdentifier,
      at: applicationURL
    ) {
      return .activated
    }

    var isDirectory: ObjCBool = false
    guard
      fileManager.fileExists(atPath: applicationURL.path, isDirectory: &isDirectory),
      isDirectory.boolValue
    else {
      return try safariFallback(reason: "la aplicación web no está instalada")
    }

    do {
      try await workspace.openApplication(at: applicationURL)
      return .opened
    } catch {
      return try safariFallback(reason: "macOS no pudo abrir la aplicación web: \(error.localizedDescription)")
    }
  }

  func stop(timeoutNanoseconds: UInt64 = 2_000_000_000) async -> ApplicationStopOutcome {
    await workspace.stopApplications(
      bundleIdentifier: Self.bundleIdentifier,
      timeoutNanoseconds: timeoutNanoseconds
    )
  }

  private func safariFallback(reason: String) throws -> WebAppLaunchOutcome {
    guard workspace.openURL(Self.webURL) else {
      throw WebAppLaunchError.fallbackFailed(reason)
    }
    return .safariFallback(reason: reason)
  }
}

struct LMStudioApplication {
  static let bundleIdentifier = LMStudioCoordinator.bundleIdentifier

  let workspace: WorkspaceOpening

  init(workspace: WorkspaceOpening = SystemWorkspace()) {
    self.workspace = workspace
  }

  func stop(timeoutNanoseconds: UInt64 = 5_000_000_000) async -> ApplicationStopOutcome {
    await workspace.stopApplications(
      bundleIdentifier: Self.bundleIdentifier,
      timeoutNanoseconds: timeoutNanoseconds
    )
  }
}

enum WebAppLaunchError: LocalizedError {
  case fallbackFailed(String)

  var errorDescription: String? {
    switch self {
    case .fallbackFailed(let reason):
      "No se pudo abrir DeutschOS ni su respaldo en Safari (\(reason))."
    }
  }
}
