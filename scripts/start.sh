#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=launcher-common.sh
source "$PROJECT_ROOT/scripts/launcher-common.sh"

STARTED_API=""
STARTED_WEB=""
LOCK_DIR="$RUN_DIR/start.lock"
LOCK_OWNED=0
START_SUCCEEDED=0
START_TIMEOUT="${DEUTSCHOS_LAUNCHER_TIMEOUT:-60}"

cleanup_started_role() {
  local role pid attempt
  role="$1"
  pid="$2"
  [[ -n "$pid" ]] || return 0
  if pid_file_state "$role" && [[ "$PID_VALUE" == "$pid" ]]; then
    stop_validated_role "$role" || true
    return 0
  fi
  if process_is_running "$pid" && expected_process "$role" "$pid"; then
    terminate_tree_signal "$pid" TERM
    attempt=0
    while ((attempt < 10)) && process_is_running "$pid"; do
      sleep 1
      attempt=$((attempt + 1))
    done
    if process_is_running "$pid"; then
      terminate_tree_signal "$pid" KILL
    fi
  fi
  rm -f -- "$(pid_file_path "$role")"
}

cleanup_start() {
  local status="$?"
  trap - EXIT HUP INT TERM
  if ((START_SUCCEEDED == 0)); then
    cleanup_started_role web "$STARTED_WEB"
    cleanup_started_role api "$STARTED_API"
  fi
  if ((LOCK_OWNED == 1)); then
    rm -f -- "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
  exit "$status"
}

trap cleanup_start EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

all_services_ready() {
  lm_studio_ready && api_ready && web_ready
}

open_deutschos() {
  local web_app
  if [[ "$LAUNCHER_TEST_MODE" == "1" && "${DEUTSCHOS_LAUNCHER_NO_OPEN:-1}" == "1" ]]; then
    launcher_log "Apertura de la interfaz omitida en modo de prueba."
    return 0
  fi
  if [[ "${DEUTSCHOS_APP_WRAPPER:-0}" == "1" ]]; then
    launcher_log "Servicios listos; el controlador abrirá la aplicación web mediante NSWorkspace."
    return 0
  fi
  web_app="$HOME/Applications/DeutschOS.app"
  if [[ -d "$web_app" ]]; then
    launcher_log "Aplicación web localizada; abriendo su bundle exacto."
    if /usr/bin/open "$web_app" >>"$LAUNCHER_LOG" 2>&1; then
      launcher_log "Aplicación web abierta."
      return 0
    fi
    launcher_log "Error de apertura de la aplicación web; fallback único a Safari."
  else
    launcher_log "Aplicación web ausente; fallback único a Safari."
  fi
  /usr/bin/open "$WEB_URL" >>"$LAUNCHER_LOG" 2>&1
}

acquire_start_lock() {
  local lock_pid lock_start actual_start command_line attempt
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n%s\n' "$$" "$(process_start_token "$$")" >"$LOCK_DIR/pid"
    LOCK_OWNED=1
    return 0
  fi

  lock_pid="$(sed -n '1p' "$LOCK_DIR/pid" 2>/dev/null || true)"
  lock_start="$(sed -n '2p' "$LOCK_DIR/pid" 2>/dev/null || true)"
  case "$lock_pid" in
    "" | *[!0-9]*) lock_pid="" ;;
  esac
  actual_start=""
  command_line=""
  if [[ -n "$lock_pid" ]] && process_is_running "$lock_pid"; then
    actual_start="$(process_start_token "$lock_pid")"
    command_line="$(ps -o command= -p "$lock_pid" 2>/dev/null || true)"
  fi
  if [[ -n "$lock_pid" && -n "$lock_start" && "$lock_start" == "$actual_start" && "$command_line" == *"scripts/start.sh"* ]]; then
    launcher_log "Otro arranque de DeutschOS está en curso; esperando su resultado."
    attempt=0
    while ((attempt < START_TIMEOUT)); do
      if all_services_ready; then
        open_deutschos || true
        return 2
      fi
      sleep 1
      attempt=$((attempt + 1))
    done
    return 1
  fi

  rm -f -- "$LOCK_DIR/pid"
  rmdir "$LOCK_DIR" 2>/dev/null || return 1
  mkdir "$LOCK_DIR" || return 1
  printf '%s\n%s\n' "$$" "$(process_start_token "$$")" >"$LOCK_DIR/pid"
  LOCK_OWNED=1
  return 0
}

ensure_launcher_directories
launcher_log "Solicitud de arranque para $PROJECT_ROOT"

[[ -d "$PROJECT_ROOT" ]] || fail_launcher "El SSD no está conectado: falta $PROJECT_ROOT."
df -P "$PROJECT_ROOT" >/dev/null 2>&1 || fail_launcher "No se puede acceder al volumen del proyecto."
[[ -x "$PYTHON" ]] || fail_launcher "Falta .venv/bin/python; ejecuta la instalación del README."
[[ -x "$NEXT_BIN" ]] || fail_launcher "Faltan dependencias web; ejecuta npm ci."
if acquire_start_lock; then
  :
else
  LOCK_RESULT=$?
  if [[ "$LOCK_RESULT" == 2 ]]; then
    START_SUCCEEDED=1
    launcher_log "DeutschOS ya quedó listo mediante el otro arranque."
    exit 0
  fi
  fail_launcher "No se pudo adquirir el bloqueo de arranque; revisa $LOCK_DIR."
fi

if lm_studio_ready; then
  launcher_log "LM Studio está activo; se reutiliza como servicio externo."
else
  fail_launcher "LM Studio no responde en $LM_STUDIO_URL. Inicia su servidor local."
fi

if ! api_ready; then
  launcher_log "Verificando dependencias y migraciones mediante dev.sh --prepare-only."
  "$PROJECT_ROOT/scripts/dev.sh" --prepare-only >>"$LAUNCHER_LOG" 2>&1 \
    || fail_launcher "La preparación de DeutschOS falló. Revisa logs/launcher.log."
fi

clean_invalid_pid_file api
if api_ready; then
  if pid_file_state api; then
    launcher_log "FastAPI ya está activa y continúa gestionada por DeutschOS."
  else
    launcher_log "FastAPI ya está activa; se reutiliza sin duplicarla ni asumir su propiedad."
  fi
elif pid_file_state api; then
  wait_for_probe "FastAPI" api_ready "$PID_VALUE" "$START_TIMEOUT" \
    || fail_launcher "La API gestionada no llegó a estar disponible. Revisa logs/api.log."
elif port_is_busy "$API_PORT"; then
  fail_launcher "El puerto $API_PORT está ocupado por un proceso ajeno a DeutschOS."
else
  launcher_log "Iniciando FastAPI en $API_URL"
  (
    cd "$PROJECT_ROOT"
    exec /usr/bin/nohup "$PYTHON" -m uvicorn deutschos_api.main:app \
      --app-dir "$PROJECT_ROOT/apps/api/src" \
      --host 127.0.0.1 \
      --port "$API_PORT" </dev/null
  ) >>"$LOG_DIR/api.log" 2>&1 &
  STARTED_API=$!
  disown "$STARTED_API" 2>/dev/null || true
  write_pid_file api "$STARTED_API" \
    || fail_launcher "No se pudo registrar el PID de FastAPI."
  wait_for_probe "FastAPI" api_ready "$STARTED_API" "$START_TIMEOUT" \
    || fail_launcher "FastAPI no arrancó. Revisa logs/api.log."
fi

clean_invalid_pid_file web
if web_ready; then
  if pid_file_state web; then
    launcher_log "Next.js ya está activa y continúa gestionada por DeutschOS."
  else
    launcher_log "Next.js ya está activa; se reutiliza sin duplicarla ni asumir su propiedad."
  fi
elif pid_file_state web; then
  wait_for_probe "Next.js" web_ready "$PID_VALUE" "$START_TIMEOUT" \
    || fail_launcher "La web gestionada no llegó a estar disponible. Revisa logs/web.log."
elif port_is_busy "$WEB_PORT"; then
  fail_launcher "El puerto $WEB_PORT está ocupado por un proceso ajeno a DeutschOS."
else
  launcher_log "Iniciando Next.js en $WEB_URL"
  /usr/bin/nohup /usr/bin/env \
    NEXT_PUBLIC_API_URL="$API_URL" \
    NEXT_TELEMETRY_DISABLED=1 \
    DO_NOT_TRACK=1 \
    "$NEXT_BIN" dev "$PROJECT_ROOT/apps/web" \
      --hostname 127.0.0.1 \
      --port "$WEB_PORT" </dev/null >>"$LOG_DIR/web.log" 2>&1 &
  STARTED_WEB=$!
  disown "$STARTED_WEB" 2>/dev/null || true
  write_pid_file web "$STARTED_WEB" \
    || fail_launcher "No se pudo registrar el PID de Next.js."
  wait_for_probe "Next.js" web_ready "$STARTED_WEB" "$START_TIMEOUT" \
    || fail_launcher "Next.js no arrancó. Revisa logs/web.log."
fi

MODEL_NAMES="$(active_model_names || true)"
[[ -n "$MODEL_NAMES" ]] || fail_launcher "LM Studio responde, pero no informa modelos disponibles."
launcher_log "Modelos activos: $MODEL_NAMES"

open_deutschos || fail_launcher "Los servicios están listos, pero macOS no pudo abrir ninguna interfaz."
START_SUCCEEDED=1
launcher_log "DeutschOS listo en $WEB_URL"
exit 0
