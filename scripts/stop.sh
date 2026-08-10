#!/usr/bin/env bash
set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=launcher-common.sh
source "$PROJECT_ROOT/scripts/launcher-common.sh"

ensure_launcher_directories
launcher_log "Inicio del apagado completo de LLC."
FAILURES=0

for ROLE in web api; do
  classify_role "$ROLE"
  case "$SERVICE_STATE" in
    managed | adopted)
      launcher_log "Parada de $ROLE · $SERVICE_STATE mediante PID $SERVICE_PID y start-token validados."
      if ! stop_validated_role "$ROLE"; then
        launcher_log "ERROR: no se pudo detener $ROLE."
        FAILURES=$((FAILURES + 1))
      fi
      ;;
    external)
      launcher_log "SEGURIDAD: $ROLE external ($SERVICE_REASON); no se envían señales."
      ;;
    stale)
      launcher_log "Registro de $ROLE stale ($SERVICE_REASON); se elimina sin enviar señales."
      rm -f -- "$(pid_file_path "$ROLE")"
      ;;
    absent)
      launcher_log "$ROLE absent; no hay proceso que detener."
      ;;
  esac
done

if port_is_busy "$LM_STUDIO_PORT"; then
  if resolve_lms_bin; then
    launcher_log "Cierre limpio del servidor local de LM Studio mediante lms server stop."
    if ! "$LMS_BIN" server stop >>"$LAUNCHER_LOG" 2>&1; then
      launcher_log "lms server stop devolvió error; se verificará el listener real."
    fi
  else
    launcher_log "No se encontró lms server stop; se verificará el listener real."
  fi
  if wait_for_port_free "LM Studio" "$LM_STUDIO_PORT" 8; then
    launcher_log "Servidor LM Studio detenido y puerto verificado."
  elif [[ "${LLC_LM_APP_WILL_CLOSE:-0}" == "1" ]]; then
    launcher_log "El listener 1234 sigue activo; el controlador cerrará ahora LM Studio.app exacta."
  elif lm_studio_listener_is_exact; then
    if ! stop_exact_lm_studio_listener; then
      launcher_log "ERROR: el listener exacto de LM Studio no pudo detenerse."
      FAILURES=$((FAILURES + 1))
    fi
  else
    launcher_log "ERROR: el puerto 1234 pertenece a un listener no verificable; no se envían señales."
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
  if ! wait_for_port_free "$LABEL" "$PORT" "$STOP_TIMEOUT"; then
    FAILURES=$((FAILURES + 1))
  fi
done
if [[ "${LLC_LM_APP_WILL_CLOSE:-0}" != "1" ]]; then
  if ! wait_for_port_free "LM Studio" "$LM_STUDIO_PORT" "$STOP_TIMEOUT"; then
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
