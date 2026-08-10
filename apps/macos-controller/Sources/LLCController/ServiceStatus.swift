import Foundation

enum ControllerPhase: Equatable {
  case checking
  case stopped
  case openingLMStudio
  case startingLMStudioServer
  case waitingLMStudio
  case startingAPI
  case startingWeb
  case openingWeb
  case running
  case stopping
  case partial
  case error
  case ssdUnavailable

  var title: String {
    switch self {
    case .checking: "Comprobando…"
    case .stopped: "Detenido"
    case .openingLMStudio: "Abriendo LM Studio"
    case .startingLMStudioServer: "Iniciando servidor de LM Studio"
    case .waitingLMStudio: "Esperando LM Studio"
    case .startingAPI: "Iniciando API"
    case .startingWeb: "Iniciando Web"
    case .openingWeb: "Abriendo LLC"
    case .running: "Activo"
    case .stopping: "Deteniendo…"
    case .partial: "Estado parcial"
    case .error: "Error"
    case .ssdUnavailable: "SSD no disponible"
    }
  }

  var isBusy: Bool {
    self == .checking || isStarting || self == .stopping
  }

  var isStarting: Bool {
    switch self {
    case .openingLMStudio, .startingLMStudioServer, .waitingLMStudio, .startingAPI,
      .startingWeb, .openingWeb:
      true
    default:
      false
    }
  }
}

enum PIDOwnership: String, Equatable {
  case managed
  case absent
  case stale
}

struct ServiceSnapshot: Equatable {
  var ssdAvailable: Bool
  var modelCount: Int
  var lm_studioActive: Bool
  var apiActive: Bool
  var webActive: Bool
  var libraryAvailable: Bool
  var librarySourceCount: Int
  var libraryPath: String
  var lm_studioPID: PIDOwnership
  var apiPID: PIDOwnership
  var webPID: PIDOwnership

  static let stopped = ServiceSnapshot(
    ssdAvailable: true,
    modelCount: 0,
    lm_studioActive: false,
    apiActive: false,
    webActive: false,
    libraryAvailable: false,
    librarySourceCount: 0,
    libraryPath: "",
    lm_studioPID: .absent,
    apiPID: .absent,
    webPID: .absent
  )

  var allServicesActive: Bool {
    lm_studioActive && apiActive && webActive
  }

  var anyServiceActive: Bool {
    lm_studioActive || apiActive || webActive
  }

  var anyManagedProcess: Bool {
    lm_studioPID == .managed || apiPID == .managed || webPID == .managed
  }

  var hasStalePID: Bool {
    lm_studioPID == .stale || apiPID == .stale || webPID == .stale
  }

  var derivedPhase: ControllerPhase {
    guard ssdAvailable else { return .ssdUnavailable }
    if allServicesActive && !hasStalePID { return .running }
    if !anyServiceActive && !hasStalePID { return .stopped }
    return .partial
  }
}

enum StatusParseError: LocalizedError {
  case unsupportedFormat
  case missingField(String)
  case invalidField(String)

  var errorDescription: String? {
    switch self {
    case .unsupportedFormat:
      "La respuesta de status.sh tiene un formato no compatible."
    case .missingField(let field):
      "status.sh no devolvió el campo \(field)."
    case .invalidField(let field):
      "status.sh devolvió un valor inválido para \(field)."
    }
  }
}

enum StatusOutputParser {
  static func parse(_ output: String) throws -> ServiceSnapshot {
    var fields: [String: String] = [:]
    for line in output.split(whereSeparator: { $0.isNewline }) {
      let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
      if parts.count == 2 {
        fields[String(parts[0])] = String(parts[1])
      }
    }

    guard fields["format"] == "deutschos-status-v1" else {
      throw StatusParseError.unsupportedFormat
    }

    func required(_ key: String) throws -> String {
      guard let value = fields[key] else { throw StatusParseError.missingField(key) }
      return value
    }

    func active(_ key: String) throws -> Bool {
      switch try required(key) {
      case "active": true
      case "inactive": false
      default: throw StatusParseError.invalidField(key)
      }
    }

    func ownership(_ key: String) throws -> PIDOwnership {
      guard let value = PIDOwnership(rawValue: try required(key)) else {
        throw StatusParseError.invalidField(key)
      }
      return value
    }

    guard let modelCount = Int(try required("model_count")), modelCount >= 0 else {
      throw StatusParseError.invalidField("model_count")
    }
    let librarySourceCount = Int(fields["library_source_count"] ?? "0") ?? 0

    return ServiceSnapshot(
      ssdAvailable: try required("ssd") == "available",
      modelCount: modelCount,
      lm_studioActive: try active("lm_studio"),
      apiActive: try active("api"),
      webActive: try active("web"),
      libraryAvailable: fields["library"] == "available",
      librarySourceCount: max(0, librarySourceCount),
      libraryPath: fields["library_path"] ?? "",
      lm_studioPID: try ownership("pid_lm_studio"),
      apiPID: try ownership("pid_api"),
      webPID: try ownership("pid_web")
    )
  }
}

enum TerminationPolicy {
  static func requiresConfirmation(
    phase: ControllerPhase,
    snapshot: ServiceSnapshot
  ) -> Bool {
    phase == .checking || phase.isStarting || phase == .stopping
      || snapshot.anyServiceActive || snapshot.anyManagedProcess
  }
}

enum ProjectLocator {
  static func locate(bundle: Bundle = .main) -> URL? {
    let configured = bundle.object(forInfoDictionaryKey: "LLCProjectRoot") as? String
    return locate(appURL: bundle.bundleURL, configuredRoot: configured)
  }

  static func locate(appURL: URL, configuredRoot: String?) -> URL? {
    let manager = FileManager.default
    let candidates = [
      configuredRoot.map { URL(fileURLWithPath: $0, isDirectory: true) },
      appURL.deletingLastPathComponent().deletingLastPathComponent(),
    ].compactMap { $0 }

    return candidates.first { candidate in
      manager.isExecutableFile(atPath: candidate.appendingPathComponent("scripts/start.sh").path)
        && manager.isExecutableFile(
          atPath: candidate.appendingPathComponent("scripts/stop.sh").path)
        && manager.isExecutableFile(
          atPath: candidate.appendingPathComponent("scripts/status.sh").path)
    }
  }
}

enum UserFacingError {
  static func message(action: String, result: ScriptResult) -> String {
    let details = result.standardError + "\n" + result.standardOutput
    let mappings: [(String, String)] = [
      ("El SSD no está conectado", "El SSD de LLC no está conectado."),
      ("No hay modelos", "No se encontró ningún modelo local de LM Studio."),
      ("LM Studio no está instalado", "LM Studio no está instalado o no se puede ejecutar."),
      ("ocupado por un proceso ajeno", "Un puerto de LLC está ocupado por otro proceso."),
      ("LM Studio no arrancó", "LM Studio no respondió antes del tiempo límite."),
      ("La API gestionada no llegó", "La API no respondió antes del tiempo límite."),
      ("FastAPI", "FastAPI no pudo iniciarse correctamente."),
      ("Next.js", "Next.js no pudo completar la acción."),
      ("no respondió antes del timeout", "Un servicio no respondió antes del tiempo límite."),
    ]
    if let match = mappings.first(where: { details.contains($0.0) }) {
      return match.1 + " Revisa los logs para obtener más información."
    }
    return "No se pudo completar \(action). Revisa los logs para obtener más información."
  }
}
