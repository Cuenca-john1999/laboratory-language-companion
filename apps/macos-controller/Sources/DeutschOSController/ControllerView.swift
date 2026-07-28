import SwiftUI

struct ControllerView: View {
  @ObservedObject var controller: ControllerModel

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      Text("DeutschOS")
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
          serviceRow("API", active: controller.snapshot.apiActive, feminine: true)
          serviceRow("Web", active: controller.snapshot.webActive, feminine: true)
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
      "DeutschOS",
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
        Button("Abrir DeutschOS") { controller.openWeb() }
          .buttonStyle(.borderedProminent)
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(!controller.canOpenWeb)
          .accessibilityIdentifier("open-deutschos")
        Button("Detener") { controller.stopRequested() }
          .controlSize(.large)
          .frame(maxWidth: .infinity)
          .disabled(!controller.canStop)
          .accessibilityIdentifier("stop")
        exitButton
      }
    case .starting, .checking:
      VStack(spacing: 10) {
        Button("Iniciando…") {}
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
          Button("Abrir DeutschOS") { controller.openWeb() }
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
    case .starting, .stopping, .checking: .orange
    case .stopped: .secondary
    case .partial, .error, .ssdUnavailable: .red
    }
  }

  private func serviceRow(_ name: String, active: Bool, feminine: Bool) -> some View {
    HStack {
      Text("\(name):")
        .frame(width: 65, alignment: .leading)
      Text(serviceText(active: active, feminine: feminine))
        .foregroundStyle(active ? .green : .secondary)
      Spacer()
    }
    .font(.body)
  }

  private func serviceText(active: Bool, feminine: Bool) -> String {
    if active { return feminine ? "activa" : "activo" }
    switch controller.phase {
    case .starting, .checking: return "esperando"
    case .stopping: return "deteniendo"
    default: return feminine ? "inactiva" : "inactivo"
    }
  }
}
