#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PACKAGE_DIR="$PROJECT_ROOT/apps/macos-controller"
BUILD_DIR="$PROJECT_ROOT/.build/macos-controller"
DIST_DIR="$PROJECT_ROOT/dist"
APP_PATH="$DIST_DIR/LLC.app"
LEGACY_APP="$DIST_DIR/legacy/LLC Launcher.app"
TEMP_APP="$DIST_DIR/.LLC-native.$$.app"
SWIFT_BIN="$(xcrun --find swift 2>/dev/null || true)"

cleanup() {
  rm -rf -- "$TEMP_APP"
}
trap cleanup EXIT HUP INT TERM

[[ -n "$SWIFT_BIN" && -x "$SWIFT_BIN" ]] || {
  printf 'Error: Swift no está disponible. Instala las Command Line Tools de Xcode.\n' >&2
  exit 1
}
[[ -f "$PACKAGE_DIR/Package.swift" ]] || {
  printf 'Error: falta apps/macos-controller/Package.swift.\n' >&2
  exit 1
}

if [[ -n "${LLC_MACOS_SDK:-${DEUTSCHOS_MACOS_SDK:-}}" ]]; then
  LLC_MACOS_SDK="${LLC_MACOS_SDK:-$DEUTSCHOS_MACOS_SDK}"
  SDK_PATH="$LLC_MACOS_SDK"
elif [[ -d /Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk ]]; then
  # CLT 26.6 can contain a 26.5 SDK built with a mismatched Swift revision.
  SDK_PATH=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk
else
  SDK_PATH="$(xcrun --show-sdk-path)"
fi
[[ -d "$SDK_PATH" ]] || {
  printf 'Error: no existe el SDK de macOS: %s\n' "$SDK_PATH" >&2
  exit 1
}

mkdir -p "$BUILD_DIR/home" "$BUILD_DIR/module-cache" "$BUILD_DIR/swiftpm-cache" "$DIST_DIR"

printf 'Compilando LLCController (release) con %s...\n' "$SDK_PATH"
HOME="$BUILD_DIR/home" \
CLANG_MODULE_CACHE_PATH="$BUILD_DIR/module-cache" \
SWIFTPM_MODULECACHE_OVERRIDE="$BUILD_DIR/swiftpm-cache" \
SDKROOT="$SDK_PATH" \
  "$SWIFT_BIN" build \
    --disable-sandbox \
    --configuration release \
    --package-path "$PACKAGE_DIR" \
    --scratch-path "$BUILD_DIR" \
    --sdk "$SDK_PATH"

BINARY_PATH="$(find "$BUILD_DIR" -type f -path '*/release/LLCController' -perm -111 | head -n 1)"
[[ -n "$BINARY_PATH" && -x "$BINARY_PATH" ]] || {
  printf 'Error: Swift terminó sin producir LLCController.\n' >&2
  exit 1
}

mkdir -p "$DIST_DIR/legacy"
if [[ -x "$APP_PATH/Contents/MacOS/applet" ]]; then
  if [[ ! -e "$LEGACY_APP" ]]; then
    mv "$APP_PATH" "$LEGACY_APP"
    printf 'Launcher anterior conservado en %s\n' "$LEGACY_APP"
  else
    rm -rf -- "$APP_PATH"
  fi
fi

# Keep the old one-click launcher reproducible under a distinct name. It also
# provides the existing lightweight icon reused by the native controller.
"$PROJECT_ROOT/scripts/create-macos-launcher.sh"

mkdir -p "$TEMP_APP/Contents/MacOS" "$TEMP_APP/Contents/Resources"
cp "$BINARY_PATH" "$TEMP_APP/Contents/MacOS/LLC"
chmod 755 "$TEMP_APP/Contents/MacOS/LLC"
cp "$PACKAGE_DIR/Resources/Info.plist" "$TEMP_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :LLCProjectRoot $PROJECT_ROOT" \
  "$TEMP_APP/Contents/Info.plist"

if [[ -f "$LEGACY_APP/Contents/Resources/applet.icns" ]]; then
  cp "$LEGACY_APP/Contents/Resources/applet.icns" \
    "$TEMP_APP/Contents/Resources/LLC.icns"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string LLC.icns' \
    "$TEMP_APP/Contents/Info.plist"
fi

/usr/bin/codesign --force --deep --sign - "$TEMP_APP"
rm -rf -- "$APP_PATH"
mv "$TEMP_APP" "$APP_PATH"
TEMP_APP=""

printf 'Aplicación nativa creada: %s\n' "$APP_PATH"
printf 'Launcher de respaldo: %s\n' "$LEGACY_APP"
