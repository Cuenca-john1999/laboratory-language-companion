import Foundation

struct ScriptResult: Equatable, Sendable {
  let exitCode: Int32
  let standardOutput: String
  let standardError: String

  var succeeded: Bool { exitCode == 0 }
}

protocol CommandRunning: AnyObject {
  func run(
    executable: URL,
    arguments: [String],
    currentDirectory: URL,
    environment additions: [String: String]
  ) async -> ScriptResult
  func cancel()
}

private final class ProcessBox: @unchecked Sendable {
  private let lock = NSLock()
  private var process: Process?

  func store(_ process: Process) {
    lock.lock()
    self.process = process
    lock.unlock()
  }

  func clear(_ candidate: Process) {
    lock.lock()
    if process === candidate { process = nil }
    lock.unlock()
  }

  func terminate() {
    lock.lock()
    let current = process
    lock.unlock()
    if current?.isRunning == true {
      current?.terminate()
    }
  }
}

final class ScriptExecutor: CommandRunning, @unchecked Sendable {
  private let processBox = ProcessBox()

  func run(
    script: URL,
    arguments: [String] = [],
    projectRoot: URL,
    environment additions: [String: String] = [:]
  ) async -> ScriptResult {
    await run(
      executable: URL(fileURLWithPath: "/bin/bash"),
      arguments: [script.path] + arguments,
      currentDirectory: projectRoot,
      environment: additions
    )
  }

  func run(
    executable: URL,
    arguments: [String] = [],
    currentDirectory: URL,
    environment additions: [String: String] = [:]
  ) async -> ScriptResult {
    let process = Process()
    let output = Pipe()
    let error = Pipe()
    process.executableURL = executable
    process.arguments = arguments
    process.currentDirectoryURL = currentDirectory
    process.standardOutput = output
    process.standardError = error
    process.qualityOfService = .utility
    process.environment = ProcessInfo.processInfo.environment.merging(additions) { _, new in new }
    processBox.store(process)

    return await withTaskCancellationHandler {
      await withCheckedContinuation { continuation in
        process.terminationHandler = { [processBox] completed in
          let standardOutput =
            String(
              data: output.fileHandleForReading.readDataToEndOfFile(),
              encoding: .utf8
            ) ?? ""
          let standardError =
            String(
              data: error.fileHandleForReading.readDataToEndOfFile(),
              encoding: .utf8
            ) ?? ""
          processBox.clear(completed)
          continuation.resume(
            returning: ScriptResult(
              exitCode: completed.terminationStatus,
              standardOutput: standardOutput,
              standardError: standardError
            )
          )
        }
        do {
          try process.run()
        } catch {
          processBox.clear(process)
          continuation.resume(
            returning: ScriptResult(
              exitCode: 127,
              standardOutput: "",
              standardError: error.localizedDescription
            )
          )
        }
      }
    } onCancel: {
      processBox.terminate()
    }
  }

  func cancel() {
    processBox.terminate()
  }
}
