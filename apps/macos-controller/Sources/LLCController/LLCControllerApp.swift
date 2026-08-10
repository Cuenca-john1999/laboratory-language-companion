import AppKit
import SwiftUI

@main
struct LLCControllerApp: App {
  @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
  @StateObject private var controller = ControllerModel.shared

  var body: some Scene {
    WindowGroup("LLC") {
      ControllerView(controller: controller)
    }
    .defaultSize(width: 390, height: 330)
    .windowResizability(.contentSize)
  }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
  private let controller = ControllerModel.shared
  private var terminationReplyPending = false

  func applicationDidFinishLaunching(_ notification: Notification) {
    NSApplication.shared.setActivationPolicy(.regular)
    NSApplication.shared.activate(ignoringOtherApps: true)
    controller.startMonitoring()
    DispatchQueue.main.async { [weak self] in
      guard let self else { return }
      for window in NSApplication.shared.windows {
        window.delegate = self
      }
    }
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    true
  }

  func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
    if controller.allowImmediateTermination {
      return .terminateNow
    }
    guard !terminationReplyPending else { return .terminateCancel }
    terminationReplyPending = true
    Task {
      await controller.refreshStatus()
      if !controller.requiresStopConfirmation {
        controller.authorizeImmediateTermination()
        terminationReplyPending = false
        sender.reply(toApplicationShouldTerminate: true)
        return
      }
      guard confirmStopAndExit() else {
        terminationReplyPending = false
        sender.reply(toApplicationShouldTerminate: false)
        return
      }
      let safe = await controller.prepareForTermination()
      terminationReplyPending = false
      if safe { controller.authorizeImmediateTermination() }
      sender.reply(toApplicationShouldTerminate: safe)
    }
    return .terminateLater
  }

  func windowShouldClose(_ sender: NSWindow) -> Bool {
    guard !terminationReplyPending else { return false }
    terminationReplyPending = true
    Task {
      await controller.refreshStatus()
      if !controller.requiresStopConfirmation {
        terminationReplyPending = false
        controller.authorizeImmediateTermination()
        NSApplication.shared.terminate(nil)
        return
      }
      guard confirmStopAndExit() else {
        terminationReplyPending = false
        return
      }
      let safe = await controller.prepareForTermination()
      terminationReplyPending = false
      if safe {
        controller.authorizeImmediateTermination()
        NSApplication.shared.terminate(nil)
      }
    }
    return false
  }

  private func confirmStopAndExit() -> Bool {
    let alert = NSAlert()
    alert.messageText = "LLC sigue en ejecución."
    alert.informativeText = "¿Quieres detener todos los servicios y salir?"
    alert.alertStyle = .warning
    alert.addButton(withTitle: "Detener y salir")
    alert.addButton(withTitle: "Cancelar")
    return alert.runModal() == .alertFirstButtonReturn
  }
}

struct WindowConfigurator: NSViewRepresentable {
  func makeNSView(context: Context) -> NSView {
    let view = NSView()
    DispatchQueue.main.async {
      if let delegate = NSApplication.shared.delegate as? AppDelegate {
        view.window?.delegate = delegate
      }
    }
    return view
  }

  func updateNSView(_ nsView: NSView, context: Context) {
    if let delegate = NSApplication.shared.delegate as? AppDelegate {
      nsView.window?.delegate = delegate
    }
  }
}
