#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DIST_DIR="$PROJECT_ROOT/dist"
LEGACY_DIR="$DIST_DIR/legacy"
APP_PATH="$LEGACY_DIR/LLC Launcher.app"
TEMP_SOURCE=""
TEMP_APP=""

cleanup() {
  if [[ -n "$TEMP_SOURCE" ]]; then
    rm -f -- "$TEMP_SOURCE"
  fi
  if [[ -n "$TEMP_APP" ]]; then
    rm -rf -- "$TEMP_APP"
  fi
  return 0
}
trap cleanup EXIT HUP INT TERM

[[ -x /usr/bin/osacompile ]] || {
  printf 'Error: osacompile no está disponible; este generador requiere macOS.\n' >&2
  exit 1
}
[[ -x "$PROJECT_ROOT/scripts/start.sh" ]] || {
  printf 'Error: falta scripts/start.sh o no es ejecutable.\n' >&2
  exit 1
}

mkdir -p "$LEGACY_DIR"
TEMP_SOURCE="$(mktemp "$LEGACY_DIR/.llc-launcher.applescript.XXXXXX")"
TEMP_APP="$LEGACY_DIR/.LLC-Launcher.$$.app"

ESCAPED_ROOT="$(printf '%s' "$PROJECT_ROOT" | sed 's/\\/\\\\/g; s/"/\\"/g')"
cat >"$TEMP_SOURCE" <<APPLESCRIPT
use scripting additions

on run
  set projectRoot to "$ESCAPED_ROOT"
  set startScript to projectRoot & "/scripts/start.sh"
  try
    do shell script ("/bin/test -d " & quoted form of projectRoot)
  on error
    display alert "LLC no está disponible" message ("Conecta el SSD y confirma que existe " & projectRoot & ".") as critical
    return
  end try
  try
    do shell script ("LLC_APP_WRAPPER=1 " & quoted form of startScript)
  on error errorMessage number errorNumber
    display alert "LLC no pudo iniciarse" message errorMessage as critical
  end try
end run
APPLESCRIPT

/usr/bin/osacompile -o "$TEMP_APP" "$TEMP_SOURCE"
/usr/libexec/PlistBuddy -c 'Set :CFBundleName LLC Launcher' "$TEMP_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Add :CFBundleDisplayName string LLC Launcher' \
  "$TEMP_APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c 'Set :CFBundleDisplayName LLC Launcher' \
    "$TEMP_APP/Contents/Info.plist"
/usr/bin/codesign --force --deep --sign - "$TEMP_APP"
rm -rf -- "$APP_PATH"
mv "$TEMP_APP" "$APP_PATH"
TEMP_APP=""

printf 'Launcher AppleScript de respaldo creado: %s\n' "$APP_PATH"
printf 'La aplicación principal se genera con scripts/build-macos-app.sh.\n'
