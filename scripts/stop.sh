#!/usr/bin/env bash
set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=launcher-common.sh
source "$PROJECT_ROOT/scripts/launcher-common.sh"

ensure_launcher_directories
launcher_log "Inicio del apagado completo de LLC."
FAILURES=0

for ROLE in api web; do
  if pid_file_state "$ROLE"; then
    launcher_log "Parada de $ROLE mediante PID y process_start_token validados."
    if ! stop_validated_role "$ROLE"; then
      launcher_log "ERROR: no se pudo detener $ROLE."
      FAILURES=$((FAILURES + 1))
    fi
  else
    STATE=$?
    if [[ "$STATE" == 2 ]]; then
      launcher_log "PID file de $ROLE obsoleto ($PID_REASON); se elimina sin enviar señales."
      rm -f -- "$(pid_file_path "$ROLE")"
    else
      launcher_log "$ROLE no tiene PID validado; no se envían señales sin demostrar identidad."
    fi
  fi
done

if port_is_busy "$LM_STUDIO_PORT"; then
  if resolve_lms_bin; then
    launcher_log "Cierre limpio del servidor local de LM Studio mediante lms server stop."
    if ! "$LMS_BIN" server stop >>"$LAUNCHER_LOG" 2>&1; then
      launcher_log "ERROR: lms server stop no pudo completar el cierre."
      FAILURES=$((FAILURES + 1))
    fi
  else
    launcher_log "ERROR: el servidor LM Studio está activo y no se encontró lms server stop."
    FAILURES=$((FAILURES + 1))
  fi
else
  launcher_log "Servidor local de LM Studio no estaba ejecutándose."
fi

rm -f -- "$RUN_DIR/start.lock/pid" 2>/dev/null || true
rmdir "$RUN_DIR/start.lock" 2>/dev/null || true

for SPEC in "FastAPI:$API_PORT" "Next.js:$WEB_PORT"; do
  LABEL="${SPEC%%:*}"
  PORT="${SPEC##*:}"
  if ! wait_for_port_free "$LABEL" "$PORT" 5; then
    FAILURES=$((FAILURES + 1))
  fi
done
if [[ "${LLC_LM_APP_WILL_CLOSE:-0}" != "1" ]]; then
  if ! wait_for_port_free "LM Studio" "$LM_STUDIO_PORT" 5; then
    FAILURES=$((FAILURES + 1))
  fi
else
  launcher_log "La verificación final de LM Studio se hará tras cerrar su aplicación exacta."
fi

if ((FAILURES > 0)); then
  launcher_log "Apagado parcial con $FAILURES error(es)."
  exit 1
fi
launcher_log "FastAPI y Next.js detenidos; servidor LM Studio detenido."
exit 0
