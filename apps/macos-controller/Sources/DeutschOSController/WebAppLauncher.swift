import AppKit
import Foundation

enum WebAppLaunchOutcome: Equatable {
  case activated
  case opened
  case safariFallback(reason: String)
}

protocol WorkspaceOpening {
  func activateApplication(bundleIdentifier: String, at url: URL) async throws -> Bool
  func openApplication(at url: URL) async throws
  func openURL(_ url: URL) -> Bool
}

struct SystemWorkspace: WorkspaceOpening {
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

  private func safariFallback(reason: String) throws -> WebAppLaunchOutcome {
    guard workspace.openURL(Self.webURL) else {
      throw WebAppLaunchError.fallbackFailed(reason)
    }
    return .safariFallback(reason: reason)
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
