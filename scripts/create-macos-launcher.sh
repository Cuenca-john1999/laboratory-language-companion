#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DIST_DIR="$PROJECT_ROOT/dist"
APP_PATH="$DIST_DIR/DeutschOS.app"
TEMP_SOURCE=""
TEMP_APP=""

cleanup() {
  [[ -n "$TEMP_SOURCE" ]] && rm -f -- "$TEMP_SOURCE"
  [[ -n "$TEMP_APP" ]] && rm -rf -- "$TEMP_APP"
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

mkdir -p "$DIST_DIR"
TEMP_SOURCE="$(mktemp "$DIST_DIR/.deutschos-launcher.XXXXXX.applescript")"
TEMP_APP="$DIST_DIR/.DeutschOS.$$.app"

ESCAPED_ROOT="$(printf '%s' "$PROJECT_ROOT" | sed 's/\\/\\\\/g; s/"/\\"/g')"
cat >"$TEMP_SOURCE" <<APPLESCRIPT
use scripting additions

on run
  set projectRoot to "$ESCAPED_ROOT"
  set startScript to projectRoot & "/scripts/start.sh"
  try
    do shell script ("/bin/test -d " & quoted form of projectRoot)
  on error
    display alert "DeutschOS no está disponible" message ("Conecta el SSD y confirma que existe " & projectRoot & ".") as critical
    return
  end try
  try
    do shell script ("DEUTSCHOS_APP_WRAPPER=1 " & quoted form of startScript)
  on error errorMessage number errorNumber
    display alert "DeutschOS no pudo iniciarse" message errorMessage as critical
  end try
end run
APPLESCRIPT

/usr/bin/osacompile -o "$TEMP_APP" "$TEMP_SOURCE"
rm -rf -- "$APP_PATH"
mv "$TEMP_APP" "$APP_PATH"
TEMP_APP=""

printf 'Launcher creado: %s\n' "$APP_PATH"
printf 'Puedes abrirlo con Finder o añadirlo al Dock.\n'
