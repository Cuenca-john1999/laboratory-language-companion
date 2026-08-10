import SwiftUI

struct ControllerView: View {
  @ObservedObject var controller: ControllerModel

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      Text("LLC")
        .font(.system(size: 25, weight: .semibold, design: .rounded))

      HStack(spacing: 9) {
        Circle()
          .fill(statusColor)
          .frame(width: 10, height: 10)
        Text("Estado: \(controller.phase.title)")
          .font(.headline)
      }

      if showsServices {
        VStack(alignment: .leading, spacing: 9) {
          serviceRow("LM Studio", active: controller.snapshot.lm_studioActive, feminine: false)
          serviceRow(
            "API",
            active: controller.snapshot.apiActive,
            feminine: true,
            ownership: controller.snapshot.apiPID
          )
          serviceRow(
            "Web",
            active: controller.snapshot.webActive,
            feminine: true,
            ownership: controller.snapshot.webPID
          )
          HStack {
            Text("Biblioteca:")
              .frame(width: 76, alignment: .leading)
            Text(
              controller.snapshot.libraryAvailable
                ? "disponible · \(controller.snapshot.librarySourceCount) fuentes"
                : "no disponible"
            )
            .foregroundStyle(controller.snapshot.libraryAvailable ? .green : .secondary)
            Spacer()
          }
          .font(.body)
        }
        .padding(.vertical, 2)
      }

      Divider()

      actionButtons

      HStack {
        Button("Abrir logs") {
          controller.openLogs()
        }
        .buttonStyle(.link)
        .accessibilityIdentifier("open-logs")
        Spacer()
        Text("Actualización cada 4 s")
          .font(.caption)
          .foregroundStyle(.secondary)
      }
    }
    .padding(24)
    .frame(width: 390)
    .background(WindowConfigurator())
    .alert(
      "LLC",
      isPresented: Binding(
        get: { controller.alertMessage != nil },
        set: { if !$0 { controller.alertMessage = nil } }
      )
    ) {
      Button("Aceptar", role: .cancel) {
        controller.alertMessage = nil
      }
    } message: {
      Text(controller.alertMessage ?? "")
    }
    .onAppear {
      controller.startMonitoring()
    }
  }

  @ViewBuilder
  private var actionButtons: some View {
    switch controller.phase {
    case .running:
      VStack(spacing: 10) {
        Button("Iniciar") { controller.startRequested() }
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(!controller.canStart)
          .accessibilityIdentifier("open-llc")
        Button("Detener") { controller.stopRequested() }
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(!controller.canStop)
          .accessibilityIdentifier("stop")
        exitButton
      }
    case .openingLMStudio, .startingLMStudioServer, .waitingLMStudio, .startingAPI,
      .startingWeb, .openingWeb:
      VStack(spacing: 10) {
        Button(controller.phase.title) {}
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(true)
        Button("Detener") { controller.stopRequested() }
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(!controller.canStop)
          .accessibilityIdentifier("stop-during-start")
        exitButton.disabled(true)
      }
    case .checking:
      VStack(spacing: 10) {
        Button("Comprobando…") {}
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(true)
        exitButton.disabled(true)
      }
    case .stopping:
      VStack(spacing: 10) {
        Button("Deteniendo…") {}
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(true)
        exitButton.disabled(true)
      }
    case .stopped:
      VStack(spacing: 10) {
        startButton
        exitButton
      }
    case .partial, .error:
      VStack(spacing: 10) {
        if controller.canOpenWeb {
          Button("Abrir LLC") { controller.openWeb() }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .frame(maxWidth: .infinity)
        }
        startButton.disabled(!controller.canStart)
        Button("Detener") { controller.stopRequested() }
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(!controller.canStop)
        exitButton
      }
    case .ssdUnavailable:
      VStack(spacing: 10) {
        Text("Conecta el SSD en /Volumes/Juegos y vuelve a abrir la aplicación.")
          .font(.callout)
          .foregroundStyle(.secondary)
        exitButton
      }
    }
  }

  private var startButton: some View {
    Button("Iniciar") { controller.startRequested() }
      .buttonStyle(.borderedProminent)
      .controlSize(.large)
      .frame(maxWidth: .infinity)
      .disabled(!controller.canStart)
      .accessibilityIdentifier("start")
  }

  private var exitButton: some View {
    Button("Salir") { controller.exitRequested() }
      .controlSize(.large)
      .frame(maxWidth: .infinity)
      .accessibilityIdentifier("exit")
  }

  private var showsServices: Bool {
    controller.phase != .stopped && controller.phase != .ssdUnavailable
  }

  private var statusColor: Color {
    switch controller.phase {
    case .running: .green
    case .openingLMStudio, .startingLMStudioServer, .waitingLMStudio, .startingAPI,
      .startingWeb, .openingWeb, .stopping, .checking:
      .orange
    case .stopped: .secondary
    case .partial, .error, .ssdUnavailable: .red
    }
  }

  private func serviceRow(
    _ name: String,
    active: Bool,
    feminine: Bool,
    ownership: PIDOwnership? = nil
  ) -> some View {
    HStack {
      Text("\(name):")
        .frame(width: 65, alignment: .leading)
      Text(serviceText(active: active, feminine: feminine, ownership: ownership))
        .foregroundStyle(active ? .green : .secondary)
      Spacer()
    }
    .font(.body)
  }

  private func serviceText(
    active: Bool,
    feminine: Bool,
    ownership: PIDOwnership?
  ) -> String {
    if ownership == .external { return "puerto ocupado · external" }
    if ownership == .stale && !active { return "inactiva · metadata stale" }
    if active {
      let state = feminine ? "activa" : "activo"
      guard let ownership else { return state }
      switch ownership {
      case .managed, .adopted: return "\(state) · LLC · \(ownership.rawValue)"
      case .external: return "\(state) · external"
      case .stale: return "\(state) · metadata stale"
      case .absent: return state
      }
    }
    switch controller.phase {
    case .openingLMStudio, .startingLMStudioServer, .waitingLMStudio, .startingAPI,
      .startingWeb, .openingWeb, .checking:
      return "esperando"
    case .stopping: return "deteniendo"
    default: return feminine ? "inactiva" : "inactivo"
    }
  }
}
