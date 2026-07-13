#!/usr/bin/env bash
set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=launcher-common.sh
source "$PROJECT_ROOT/scripts/launcher-common.sh"

ensure_launcher_directories
launcher_log "Solicitud de cierre para procesos gestionados por DeutschOS."
FAILURES=0

for ROLE in web api ollama; do
  if pid_file_state "$ROLE"; then
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
      launcher_log "$ROLE no fue iniciado por el launcher; no se detiene ningún proceso externo."
    fi
  fi
done

rm -f -- "$RUN_DIR/start.lock/pid" 2>/dev/null || true
rmdir "$RUN_DIR/start.lock" 2>/dev/null || true

if ((FAILURES > 0)); then
  launcher_log "Cierre completado con $FAILURES error(es)."
  exit 1
fi
launcher_log "Cierre completado; los servicios externos, si existen, no fueron modificados."
exit 0
