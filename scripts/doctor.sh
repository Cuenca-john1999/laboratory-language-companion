#!/usr/bin/env bash

# Intentionally avoid `set -e`: a doctor must report every independent check.
set -u
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CONFIG_HELPER="$ROOT/scripts/local_config.py"
FAILURES=0
WARNINGS=0

ok() {
  printf '✓ %s\n' "$1"
}

warn() {
  WARNINGS=$((WARNINGS + 1))
  printf '! %s\n' "$1"
}

bad() {
  FAILURES=$((FAILURES + 1))
  printf '✗ %s\n' "$1"
}

detail() {
  printf '  %s\n' "$1"
}

command_version() {
  "$1" --version 2>&1 | head -n 1
}

printf 'DeutschOS doctor\n'
printf 'Proyecto: %s\n\n' "$ROOT"

printf 'Volumen y datos\n'
if VOLUME_INFO="$(df -Pk "$ROOT" 2>&1 | tail -n 1)"; then
  ok "volumen disponible"
  detail "$VOLUME_INFO"
else
  bad "no se pudo consultar el volumen del proyecto"
  detail "$VOLUME_INFO"
fi

case "$ROOT" in
  /Volumes/*)
    ok "el proyecto está en un volumen montado bajo /Volumes"
    ;;
  *)
    warn "el proyecto no está bajo /Volumes; es válido, pero no parece el SSD externo esperado"
    ;;
esac

WRITE_PROBE=""
cleanup_probe() {
  if [[ -n "$WRITE_PROBE" && -e "$WRITE_PROBE" ]]; then
    rm -f -- "$WRITE_PROBE"
  fi
}
trap cleanup_probe EXIT
trap 'cleanup_probe; exit 129' HUP
trap 'cleanup_probe; exit 130' INT
trap 'cleanup_probe; exit 143' TERM

if [[ -d "$ROOT/data" ]]; then
  if WRITE_PROBE="$(mktemp "$ROOT/data/.doctor.XXXXXX" 2>/dev/null)"; then
    cleanup_probe
    WRITE_PROBE=""
    ok "data/ admite escritura real"
  else
    bad "data/ no admite escritura"
  fi
else
  warn "data/ todavía no existe; dev.sh lo creará"
fi

printf '\nRuntime JavaScript\n'
if command -v node >/dev/null 2>&1; then
  NODE_VERSION="$(node -p 'process.versions.node' 2>/dev/null || true)"
  if node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 20 || (major === 20 && minor >= 9) ? 0 : 1)'; then
    ok "Node.js $NODE_VERSION (>=20.9)"
  else
    bad "Node.js 20.9 o posterior requerido; detectado: ${NODE_VERSION:-desconocido}"
  fi
else
  bad "Node.js no está instalado"
fi

if command -v npm >/dev/null 2>&1; then
  ok "npm $(npm --version 2>/dev/null || printf desconocido)"
else
  bad "npm no está instalado"
fi

if command -v npm >/dev/null 2>&1 && [[ -d "$ROOT/node_modules" ]]; then
  if NPM_CHECK="$(cd "$ROOT" && npm ls --depth=0 2>&1)"; then
    ok "dependencias npm instaladas y coherentes"
  else
    bad "npm informa dependencias ausentes o inválidas"
    detail "$NPM_CHECK"
  fi
else
  bad "faltan dependencias npm; ejecuta npm ci"
fi

printf '\nRuntime Python\n'
SYSTEM_PYTHON=""
if command -v python3.12 >/dev/null 2>&1; then
  SYSTEM_PYTHON="$(command -v python3.12)"
  PYTHON_VERSION="$($SYSTEM_PYTHON --version 2>&1)"
  if "$SYSTEM_PYTHON" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
    ok "$PYTHON_VERSION"
  else
    bad "python3.12 no ejecuta Python 3.12"
  fi
else
  bad "Python 3.12 no está instalado"
fi

VENV_PYTHON="$ROOT/.venv/bin/python"
if [[ -x "$VENV_PYTHON" ]]; then
  if "$VENV_PYTHON" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12) or sys.prefix == sys.base_prefix)'; then
    ok "entorno virtual .venv activo y basado en Python 3.12"
  else
    bad ".venv existe pero no es un entorno Python 3.12 válido"
  fi

  if PIP_CHECK="$($VENV_PYTHON -m pip check 2>&1)"; then
    ok "dependencias Python sin conflictos declarados"
  else
    bad "pip check encontró dependencias rotas"
    detail "$PIP_CHECK"
  fi

  if IMPORT_CHECK="$($VENV_PYTHON -c 'import alembic, fastapi, httpx, pydantic_settings, sqlalchemy, uvicorn' 2>&1)"; then
    ok "dependencias esenciales de la API importables"
  else
    bad "faltan dependencias esenciales de la API"
    detail "$IMPORT_CHECK"
  fi
else
  bad "falta .venv; créalo e instala apps/api[dev]"
fi

CONFIG_PYTHON="$SYSTEM_PYTHON"
if [[ -x "$VENV_PYTHON" ]]; then
  CONFIG_PYTHON="$VENV_PYTHON"
fi

printf '\nBase de datos y migraciones\n'
DATABASE_PATH=""
if [[ -n "$CONFIG_PYTHON" ]]; then
  if DATABASE_PATH="$($CONFIG_PYTHON "$CONFIG_HELPER" --root "$ROOT" database-path 2>&1)"; then
    detail "SQLite: $DATABASE_PATH"
  else
    bad "no se pudo resolver DEUTSCHOS_DATABASE_URL"
    detail "$DATABASE_PATH"
    DATABASE_PATH=""
  fi
else
  bad "no hay Python disponible para resolver la configuración local"
fi

if [[ -n "$DATABASE_PATH" && -f "$DATABASE_PATH" ]]; then
  if SQLITE_CHECK="$($CONFIG_PYTHON -c '
import sqlite3, sys
from pathlib import Path
uri = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
with sqlite3.connect(uri, uri=True, timeout=5) as connection:
    print(connection.execute("PRAGMA quick_check").fetchone()[0])
' "$DATABASE_PATH" 2>&1)" && [[ "$SQLITE_CHECK" == "ok" ]]; then
    ok "integridad SQLite (quick_check)"
  else
    bad "SQLite no superó quick_check"
    detail "$SQLITE_CHECK"
  fi

  if [[ -x "$VENV_PYTHON" ]]; then
    if HEAD_OUTPUT="$(cd "$ROOT" && "$VENV_PYTHON" -m alembic -c apps/api/alembic.ini heads 2>&1)"; then
      HEAD_REVISIONS="$(printf '%s\n' "$HEAD_OUTPUT" | awk '$1 != "INFO" && $1 ~ /^[[:alnum:]_]+$/ {print $1}' | sort | tr '\n' ' ' | sed 's/ $//')"
    else
      HEAD_REVISIONS=""
      bad "Alembic no pudo determinar el head"
      detail "$HEAD_OUTPUT"
    fi

    if CURRENT_OUTPUT="$(cd "$ROOT" && "$VENV_PYTHON" -m alembic -c apps/api/alembic.ini current 2>&1)"; then
      CURRENT_REVISIONS="$(printf '%s\n' "$CURRENT_OUTPUT" | awk '$1 != "INFO" && $1 ~ /^[[:alnum:]_]+$/ {print $1}' | sort | tr '\n' ' ' | sed 's/ $//')"
      if [[ -n "$HEAD_REVISIONS" && "$CURRENT_REVISIONS" == "$HEAD_REVISIONS" ]]; then
        ok "migraciones al día ($CURRENT_REVISIONS)"
      elif [[ -z "$CURRENT_REVISIONS" ]]; then
        warn "la base no registra una revisión Alembic; dev.sh aplicará migraciones"
      else
        warn "migraciones pendientes: actual=$CURRENT_REVISIONS head=${HEAD_REVISIONS:-desconocido}"
      fi
    else
      bad "Alembic no pudo leer la revisión de la base configurada"
      detail "$CURRENT_OUTPUT"
    fi
  else
    bad "no se pueden comprobar migraciones sin .venv"
  fi
elif [[ -n "$DATABASE_PATH" ]]; then
  warn "la base todavía no existe; dev.sh la creará mediante Alembic"
fi

printf '\nOllama (opcional)\n'
if command -v ollama >/dev/null 2>&1; then
  ok "Ollama instalado: $(command_version ollama)"
else
  warn "Ollama no está instalado; API y web seguirán funcionando sin chat local"
fi

OLLAMA_URL="http://127.0.0.1:11434"
if [[ -n "$CONFIG_PYTHON" ]]; then
  if ! OLLAMA_URL="$($CONFIG_PYTHON "$CONFIG_HELPER" --root "$ROOT" value DEUTSCHOS_OLLAMA_BASE_URL --default "$OLLAMA_URL" 2>&1)"; then
    bad "no se pudo leer DEUTSCHOS_OLLAMA_BASE_URL"
    detail "$OLLAMA_URL"
    OLLAMA_URL=""
  fi
fi

if [[ -n "$OLLAMA_URL" && -n "$CONFIG_PYTHON" ]]; then
  if "$CONFIG_PYTHON" "$CONFIG_HELPER" --root "$ROOT" is-loopback "$OLLAMA_URL"; then
    ok "Ollama configurado en loopback ($OLLAMA_URL)"
    if OLLAMA_TAGS="$(curl --silent --show-error --fail --connect-timeout 1 --max-time 3 "$OLLAMA_URL/api/tags" 2>&1)"; then
      ok "servidor Ollama disponible"
      if MODEL_COUNT="$(printf '%s' "$OLLAMA_TAGS" | "$CONFIG_PYTHON" -c '
import json, sys
payload = json.load(sys.stdin)
models = payload.get("models", [])
if not isinstance(models, list):
    raise SystemExit(2)
print(len(models))
' 2>/dev/null)"; then
        if ((MODEL_COUNT > 0)); then
          ok "$MODEL_COUNT modelo(s) instalado(s) en Ollama"
        else
          warn "Ollama responde pero no tiene modelos instalados"
        fi
      else
        warn "Ollama respondió con un inventario de modelos no válido"
      fi
    else
      warn "servidor Ollama apagado o inaccesible; el resto de la aplicación puede arrancar"
    fi
  else
    bad "DEUTSCHOS_OLLAMA_BASE_URL debe apuntar a localhost/loopback"
  fi
elif [[ -z "$CONFIG_PYTHON" ]]; then
  warn "se omite la comprobación de URL de Ollama porque Python no está disponible"
fi

printf '\nPuertos de desarrollo\n'
check_port() {
  port="$1"
  service="$2"
  if command -v lsof >/dev/null 2>&1; then
    LISTENERS="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$LISTENERS" ]]; then
      warn "puerto $port ocupado ($service); puede que el servicio ya esté activo"
      printf '%s\n' "$LISTENERS" | sed -n '1,4p' | sed 's/^/  /'
    else
      ok "puerto $port libre ($service)"
    fi
  elif command -v nc >/dev/null 2>&1; then
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      warn "puerto $port ocupado ($service)"
    else
      ok "puerto $port libre ($service)"
    fi
  else
    warn "no se pudo comprobar el puerto $port: faltan lsof y nc"
  fi
}

check_port 3000 "Next.js"
check_port 8000 "FastAPI"

printf '\nResumen: %s fallo(s), %s aviso(s).\n' "$FAILURES" "$WARNINGS"
if ((FAILURES > 0)); then
  exit 1
fi
exit 0
