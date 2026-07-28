#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE_DIR="$PROJECT_ROOT/apps/macos-controller/Sources/DeutschOSController"
TEST_SOURCE="$PROJECT_ROOT/apps/macos-controller/Tests/ControllerTests.swift"
BUILD_DIR="$PROJECT_ROOT/.build/macos-controller-tests"
SWIFTC="$(xcrun --find swiftc 2>/dev/null || true)"

[[ -n "$SWIFTC" && -x "$SWIFTC" ]] || {
  printf 'Error: swiftc no está disponible.\n' >&2
  exit 1
}

if [[ -n "${DEUTSCHOS_MACOS_SDK:-}" ]]; then
  SDK_PATH="$DEUTSCHOS_MACOS_SDK"
elif [[ -d /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk ]]; then
  SDK_PATH=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk
else
  SDK_PATH="$(xcrun --show-sdk-path)"
fi

mkdir -p "$BUILD_DIR/module-cache"
TARGET="$(uname -m)-apple-macosx13.0"

"$SWIFTC" \
  -sdk "$SDK_PATH" \
  -target "$TARGET" \
  -module-cache-path "$BUILD_DIR/module-cache" \
  "$SOURCE_DIR/ServiceStatus.swift" \
  "$SOURCE_DIR/ScriptExecutor.swift" \
  "$SOURCE_DIR/WebAppLauncher.swift" \
  "$SOURCE_DIR/LMStudioCoordinator.swift" \
  "$SOURCE_DIR/ControllerModel.swift" \
  "$TEST_SOURCE" \
  -o "$BUILD_DIR/ControllerTests"

if [[ "${1:-}" == "--integration" ]]; then
  DEUTSCHOS_CONTROLLER_INTEGRATION_ROOT="$PROJECT_ROOT" "$BUILD_DIR/ControllerTests"
else
  "$BUILD_DIR/ControllerTests"
fi
