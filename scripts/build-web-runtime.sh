#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
RUNTIME_ROOT="${LLC_LAUNCHER_WEB_RUNTIME_ROOT:-$PROJECT_ROOT/var/desktop-web}"
RELEASES_DIR="$RUNTIME_ROOT/releases"
WEB_BUILD="$PROJECT_ROOT/apps/web/.next"

mkdir -p "$RELEASES_DIR"
STAGING_DIR="$(mktemp -d "$RUNTIME_ROOT/.staging.XXXXXX")"
RELEASE_NAME="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
RELEASE_DIR="$RELEASES_DIR/$RELEASE_NAME"

printf 'Construyendo el runtime web de producción aislado…\n'
(
  cd "$PROJECT_ROOT"
  NEXT_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 npm run build:web
)

[[ -f "$WEB_BUILD/BUILD_ID" ]] || {
  printf 'La compilación de Next no produjo BUILD_ID.\n' >&2
  exit 1
}
[[ -d "$WEB_BUILD/standalone" ]] || {
  printf 'La compilación de Next no produjo el runtime standalone.\n' >&2
  exit 1
}

cp -R "$WEB_BUILD/standalone/." "$STAGING_DIR/"
if [[ -f "$STAGING_DIR/apps/web/server.js" ]]; then
  SERVER_RELATIVE="apps/web"
elif [[ -f "$STAGING_DIR/server.js" ]]; then
  SERVER_RELATIVE="."
else
  printf 'No se encontró server.js en el runtime standalone.\n' >&2
  exit 1
fi

mkdir -p "$STAGING_DIR/$SERVER_RELATIVE/.next"
cp -R "$WEB_BUILD/static" "$STAGING_DIR/$SERVER_RELATIVE/.next/static"
if [[ -d "$PROJECT_ROOT/apps/web/public" ]]; then
  cp -R "$PROJECT_ROOT/apps/web/public" "$STAGING_DIR/$SERVER_RELATIVE/public"
fi
printf '%s\n' "$SERVER_RELATIVE" >"$STAGING_DIR/server-relative"
printf '%s\n' "$(sed -n '1p' "$WEB_BUILD/BUILD_ID")" >"$STAGING_DIR/build-id"

mv "$STAGING_DIR" "$RELEASE_DIR"
MANIFEST_TEMP="$RUNTIME_ROOT/current-release.$$"
printf '%s\n' "$RELEASE_NAME" >"$MANIFEST_TEMP"
mv -f "$MANIFEST_TEMP" "$RUNTIME_ROOT/current-release"
chmod -R u=rwX,go= "$RELEASE_DIR"

printf 'Runtime web inmutable listo: %s\n' "$RELEASE_DIR"
