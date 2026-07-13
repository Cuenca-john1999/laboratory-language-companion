#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="$ROOT/.venv/bin/python"
CONFIG_HELPER="$ROOT/scripts/local_config.py"
API_PID=""
WEB_PID=""
PREPARE_ONLY=0

if [[ "${1:-}" == "--prepare-only" ]]; then
  PREPARE_ONLY=1
  shift
fi
[[ "$#" == 0 ]] || {
  printf 'Uso: %s [--prepare-only]\n' "$0" >&2
  exit 2
}

fail() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "no se encontró '$1'. Ejecuta ./scripts/doctor.sh."
}

extract_revisions() {
  awk '$1 != "INFO" && $1 ~ /^[[:alnum:]_]+$/ {print $1}' | sort | tr '\n' ' ' | sed 's/ $//'
}

port_is_busy() {
  local port
  port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" >/dev/null 2>&1
    return $?
  fi
  fail "no se pueden comprobar puertos: faltan lsof y nc"
}

terminate_tree() {
  local parent_pid child_pid
  parent_pid="$1"
  if command -v pgrep >/dev/null 2>&1; then
    for child_pid in $(pgrep -P "$parent_pid" 2>/dev/null || true); do
      terminate_tree "$child_pid"
    done
  fi
  if kill -0 "$parent_pid" >/dev/null 2>&1; then
    kill -TERM "$parent_pid" >/dev/null 2>&1 || true
  fi
}

process_is_running() {
  local process_pid process_state
  process_pid="$1"
  if ! kill -0 "$process_pid" >/dev/null 2>&1; then
    return 1
  fi
  process_state="$(ps -o stat= -p "$process_pid" 2>/dev/null | tr -d '[:space:]')"
  case "$process_state" in
    "" | Z*) return 1 ;;
    *) return 0 ;;
  esac
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [[ -n "$API_PID" || -n "$WEB_PID" ]]; then
    printf '\nCerrando DeutschOS...\n'
  fi
  if [[ -n "$WEB_PID" ]]; then
    terminate_tree "$WEB_PID"
  fi
  if [[ -n "$API_PID" ]]; then
    terminate_tree "$API_PID"
  fi

  attempts=0
  while ((attempts < 10)); do
    api_alive=0
    web_alive=0
    [[ -n "$API_PID" ]] && process_is_running "$API_PID" && api_alive=1
    [[ -n "$WEB_PID" ]] && process_is_running "$WEB_PID" && web_alive=1
    if ((api_alive == 0 && web_alive == 0)); then
      break
    fi
    sleep 1
    attempts=$((attempts + 1))
  done

  if [[ -n "$WEB_PID" ]] && process_is_running "$WEB_PID"; then
    kill -KILL "$WEB_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$API_PID" ]] && process_is_running "$API_PID"; then
    kill -KILL "$API_PID" >/dev/null 2>&1 || true
  fi
  [[ -n "$WEB_PID" ]] && wait "$WEB_PID" >/dev/null 2>&1 || true
  [[ -n "$API_PID" ]] && wait "$API_PID" >/dev/null 2>&1 || true
  exit "$status"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT"
printf 'Preparando DeutschOS en %s\n' "$ROOT"

require_command node
require_command npm
require_command curl
[[ -x "$PYTHON" ]] || fail "falta .venv/bin/python. Ejecuta la instalación indicada en README.md."
"$PYTHON" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' \
  || fail ".venv debe usar Python 3.12."
"$PYTHON" -c 'import alembic, fastapi, httpx, pydantic_settings, sqlalchemy, uvicorn' \
  || fail "faltan dependencias Python. Instala apps/api[dev]."
npm ls --depth=0 >/dev/null || fail "faltan dependencias npm. Ejecuta npm ci."

node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)' \
  || fail "se necesita Node.js 20.9 o posterior."

mkdir -p "$ROOT/data" || fail "no se pudo crear data/."
DATABASE_PATH="$($PYTHON "$CONFIG_HELPER" --root "$ROOT" database-path)" \
  || fail "DEUTSCHOS_DATABASE_URL no es una URL SQLite local válida."
mkdir -p "$(dirname "$DATABASE_PATH")" \
  || fail "no se pudo crear el directorio de la base configurada."

WRITE_PROBE="$(mktemp "$(dirname "$DATABASE_PATH")/.deutschos-write.XXXXXX" 2>/dev/null)" \
  || fail "no hay permisos de escritura junto a la base configurada."
rm -f -- "$WRITE_PROBE"

PUBLIC_API_URL="$($PYTHON "$CONFIG_HELPER" --root "$ROOT" value NEXT_PUBLIC_API_URL --default 'http://127.0.0.1:8000')"
"$PYTHON" "$CONFIG_HELPER" --root "$ROOT" is-loopback "$PUBLIC_API_URL" \
  || fail "NEXT_PUBLIC_API_URL debe apuntar a localhost/loopback."
PUBLIC_API_PORT="$($PYTHON -c '
import sys
from urllib.parse import urlsplit
parsed = urlsplit(sys.argv[1])
print(parsed.port or (443 if parsed.scheme == "https" else 80))
' "$PUBLIC_API_URL")"
[[ "$PUBLIC_API_PORT" == "8000" ]] \
  || fail "dev.sh inicia FastAPI en 8000; NEXT_PUBLIC_API_URL debe usar ese puerto."

OLLAMA_URL="$($PYTHON "$CONFIG_HELPER" --root "$ROOT" value DEUTSCHOS_OLLAMA_BASE_URL --default 'http://127.0.0.1:11434')"
"$PYTHON" "$CONFIG_HELPER" --root "$ROOT" is-loopback "$OLLAMA_URL" \
  || fail "DEUTSCHOS_OLLAMA_BASE_URL debe apuntar a localhost/loopback."

export NEXT_PUBLIC_API_URL="$PUBLIC_API_URL"
export NEXT_TELEMETRY_DISABLED=1
export DO_NOT_TRACK=1

if ((PREPARE_ONLY == 0)); then
  if port_is_busy 8000; then
    fail "el puerto 8000 ya está ocupado. Cierra el proceso indicado por ./scripts/doctor.sh."
  fi
  if port_is_busy 3000; then
    fail "el puerto 3000 ya está ocupado. Cierra el proceso indicado por ./scripts/doctor.sh."
  fi
fi

if [[ -f "$DATABASE_PATH" ]]; then
  SQLITE_STATUS="$($PYTHON -c '
import sqlite3, sys
from pathlib import Path
uri = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
with sqlite3.connect(uri, uri=True, timeout=5) as connection:
    print(connection.execute("PRAGMA quick_check").fetchone()[0])
' "$DATABASE_PATH")"
  [[ "$SQLITE_STATUS" == "ok" ]] || fail "la base SQLite no superó quick_check; no se migrará."
fi

HEAD_OUTPUT="$($PYTHON -m alembic -c apps/api/alembic.ini heads 2>&1)" \
  || fail "Alembic no pudo determinar el head:\n$HEAD_OUTPUT"
HEAD_REVISIONS="$(printf '%s\n' "$HEAD_OUTPUT" | extract_revisions)"
HEAD_COUNT="$(printf '%s\n' "$HEAD_REVISIONS" | awk '{print NF}')"
[[ "$HEAD_COUNT" == "1" ]] || fail "se esperaba un único head Alembic; detectado: $HEAD_REVISIONS"

CURRENT_REVISIONS=""
if [[ -f "$DATABASE_PATH" ]]; then
  CURRENT_OUTPUT="$($PYTHON -m alembic -c apps/api/alembic.ini current 2>&1)" \
    || fail "Alembic no reconoce la revisión actual:\n$CURRENT_OUTPUT"
  CURRENT_REVISIONS="$(printf '%s\n' "$CURRENT_OUTPUT" | extract_revisions)"

  if [[ -z "$CURRENT_REVISIONS" ]]; then
    USER_TABLE_COUNT="$($PYTHON -c '
import sqlite3, sys
from pathlib import Path
uri = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
with sqlite3.connect(uri, uri=True, timeout=5) as connection:
    row = connection.execute(
        "SELECT count(*) FROM sqlite_schema "
        "WHERE type = '\''table'\'' AND name NOT LIKE '\''sqlite_%'\'' "
        "AND name != '\''alembic_version'\''"
    ).fetchone()
    print(row[0])
' "$DATABASE_PATH")"
    ((USER_TABLE_COUNT == 0)) \
      || fail "la base contiene tablas pero no una revisión Alembic; se preserva sin modificar."
  fi
fi

if [[ "$CURRENT_REVISIONS" != "$HEAD_REVISIONS" ]]; then
  if [[ -s "$DATABASE_PATH" ]]; then
    printf 'Creando backup previo a la migración...\n'
    "$ROOT/scripts/backup.sh" --database-only --label pre-migration
  fi
  printf 'Aplicando migraciones Alembic hasta %s...\n' "$HEAD_REVISIONS"
  "$PYTHON" -m alembic -c apps/api/alembic.ini upgrade head
else
  printf 'Migraciones al día (%s).\n' "$HEAD_REVISIONS"
fi

POST_OUTPUT="$($PYTHON -m alembic -c apps/api/alembic.ini current 2>&1)" \
  || fail "no se pudo verificar la revisión tras migrar:\n$POST_OUTPUT"
POST_REVISIONS="$(printf '%s\n' "$POST_OUTPUT" | extract_revisions)"
[[ "$POST_REVISIONS" == "$HEAD_REVISIONS" ]] \
  || fail "la revisión final ($POST_REVISIONS) no coincide con head ($HEAD_REVISIONS)."

if ((PREPARE_ONLY == 1)); then
  printf 'Preparación local completada; no se iniciaron servidores.\n'
  exit 0
fi

printf '\nIniciando FastAPI en http://127.0.0.1:8000\n'
"$PYTHON" -m uvicorn deutschos_api.main:app \
  --app-dir "$ROOT/apps/api/src" \
  --host 127.0.0.1 \
  --port 8000 \
  --reload \
  --reload-dir "$ROOT/apps/api" &
API_PID=$!

printf 'Iniciando Next.js en http://127.0.0.1:3000\n'
npm --workspace @deutschos/web run dev -- --port 3000 &
WEB_PID=$!

wait_for_url() {
  local label url attempt
  label="$1"
  url="$2"
  attempt=0
  while ((attempt < 30)); do
    if curl --silent --fail --connect-timeout 1 --max-time 2 "$url" >/dev/null 2>&1; then
      printf '✓ %s listo: %s\n' "$label" "$url"
      return 0
    fi
    if ! process_is_running "$API_PID" || ! process_is_running "$WEB_PID"; then
      return 1
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  return 1
}

wait_for_url "FastAPI" "http://127.0.0.1:8000/health" \
  || fail "FastAPI no alcanzó un estado listo."
wait_for_url "Next.js" "http://127.0.0.1:3000" \
  || fail "Next.js no alcanzó un estado listo."

printf '\nDeutschOS está listo. Pulsa Ctrl-C para cerrar ambos procesos.\n'
EXIT_STATUS=0
while :; do
  if ! process_is_running "$API_PID"; then
    wait "$API_PID" || EXIT_STATUS=$?
    printf 'FastAPI terminó; se cerrará Next.js.\n' >&2
    break
  fi
  if ! process_is_running "$WEB_PID"; then
    wait "$WEB_PID" || EXIT_STATUS=$?
    printf 'Next.js terminó; se cerrará FastAPI.\n' >&2
    break
  fi
  sleep 1
done

exit "$EXIT_STATUS"
