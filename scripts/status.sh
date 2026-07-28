#!/usr/bin/env bash
set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=launcher-common.sh
source "$PROJECT_ROOT/scripts/launcher-common.sh"

ensure_launcher_directories

if [[ "${1:-}" == "--machine" ]]; then
  MACHINE_FAILURES=0
  ACTIVE_SERVICES=0
  STALE_PID_FILES=0

  printf 'format=deutschos-status-v1\n'
  if [[ -d "$PROJECT_ROOT" ]] && df -P "$PROJECT_ROOT" >/dev/null 2>&1; then
    printf 'ssd=available\n'
  else
    printf 'ssd=unavailable\n'
    MACHINE_FAILURES=$((MACHINE_FAILURES + 1))
  fi

  MODEL_NAMES="$(active_model_names || true)"
  if [[ -n "$MODEL_NAMES" ]]; then
    printf 'model_count=%s\n' "$(printf '%s\n' "$MODEL_NAMES" | awk -F', ' '{print NF}')"
  else
    printf 'model_count=0\n'
  fi

  LIBRARY_MATERIALS_DIR="${DEUTSCHOS_EDUCATIONAL_MATERIALS_DIR:-$PROJECT_ROOT/material educativo}"
  LIBRARY_RUNTIME_DIR="${DEUTSCHOS_EDUCATIONAL_LIBRARY_RUNTIME_DIR:-$PROJECT_ROOT/var/educational-library}"
  printf 'library_path=%s\n' "$LIBRARY_MATERIALS_DIR"
  if [[ -d "$LIBRARY_MATERIALS_DIR" ]]; then
    printf 'library=available\n'
  else
    printf 'library=unavailable\n'
  fi
  LIBRARY_SOURCE_COUNT=0
  if [[ -f "$LIBRARY_RUNTIME_DIR/library.sqlite3" ]] && command -v sqlite3 >/dev/null 2>&1; then
    LIBRARY_SOURCE_COUNT="$(sqlite3 "$LIBRARY_RUNTIME_DIR/library.sqlite3" \
      'SELECT count(*) FROM sources;' 2>/dev/null || printf '0')"
  fi
  case "$LIBRARY_SOURCE_COUNT" in
    '' | *[!0-9]*) LIBRARY_SOURCE_COUNT=0 ;;
  esac
  printf 'library_source_count=%s\n' "$LIBRARY_SOURCE_COUNT"

  if lm_studio_ready; then
    printf 'lm_studio=active\n'
    ACTIVE_SERVICES=$((ACTIVE_SERVICES + 1))
  else
    printf 'lm_studio=inactive\n'
    MACHINE_FAILURES=$((MACHINE_FAILURES + 1))
  fi
  printf 'pid_lm_studio=absent\n'
  if api_ready; then
    printf 'api=active\n'
    ACTIVE_SERVICES=$((ACTIVE_SERVICES + 1))
  else
    printf 'api=inactive\n'
    MACHINE_FAILURES=$((MACHINE_FAILURES + 1))
  fi
  if web_ready; then
    printf 'web=active\n'
    ACTIVE_SERVICES=$((ACTIVE_SERVICES + 1))
  else
    printf 'web=inactive\n'
    MACHINE_FAILURES=$((MACHINE_FAILURES + 1))
  fi

  for ROLE in api web; do
    if pid_file_state "$ROLE"; then
      printf 'pid_%s=managed\n' "$ROLE"
      printf 'pid_%s_value=%s\n' "$ROLE" "$PID_VALUE"
    else
      STATE=$?
      if [[ "$STATE" == 2 ]]; then
        printf 'pid_%s=stale\n' "$ROLE"
        STALE_PID_FILES=$((STALE_PID_FILES + 1))
        MACHINE_FAILURES=$((MACHINE_FAILURES + 1))
      else
        printf 'pid_%s=absent\n' "$ROLE"
      fi
    fi
  done

  if ((ACTIVE_SERVICES == 3 && STALE_PID_FILES == 0)); then
    printf 'result=running\n'
  elif ((ACTIVE_SERVICES == 0 && STALE_PID_FILES == 0)); then
    printf 'result=stopped\n'
  else
    printf 'result=partial\n'
  fi
  if ((MACHINE_FAILURES > 0)); then
    exit 1
  fi
  exit 0
fi

FAILURES=0
STALE_PID_FILES=0

printf 'DeutschOS launcher status\n'
printf 'Proyecto: %s\n' "$PROJECT_ROOT"

if [[ -d "$PROJECT_ROOT" ]] && df -P "$PROJECT_ROOT" >/dev/null 2>&1; then
  printf '✓ SSD disponible\n'
else
  printf '✗ SSD no disponible\n'
  FAILURES=$((FAILURES + 1))
fi

LIBRARY_MATERIALS_DIR="${DEUTSCHOS_EDUCATIONAL_MATERIALS_DIR:-$PROJECT_ROOT/material educativo}"
if [[ -d "$LIBRARY_MATERIALS_DIR" ]]; then
  printf '✓ Biblioteca educativa disponible (%s)\n' "$LIBRARY_MATERIALS_DIR"
else
  printf '! Biblioteca educativa no disponible (%s)\n' "$LIBRARY_MATERIALS_DIR"
fi

if lm_studio_ready; then
  MODEL_NAMES="$(active_model_names || true)"
  printf '✓ LM Studio activo%s\n' "${MODEL_NAMES:+ — $MODEL_NAMES}"
else
  printf '✗ LM Studio inactivo (%s)\n' "$LM_STUDIO_URL"
  FAILURES=$((FAILURES + 1))
fi

if api_ready; then
  printf '✓ API activa (%s/health)\n' "$API_URL"
else
  printf '✗ API inactiva (%s/health)\n' "$API_URL"
  FAILURES=$((FAILURES + 1))
fi

if web_ready; then
  printf '✓ Web activa (%s)\n' "$WEB_URL"
else
  printf '✗ Web inactiva (%s)\n' "$WEB_URL"
  FAILURES=$((FAILURES + 1))
fi

printf '\nPID files\n'
for ROLE in api web; do
  if pid_file_state "$ROLE"; then
    printf '✓ %s: PID %s válido y gestionado\n' "$ROLE" "$PID_VALUE"
  else
    STATE=$?
    if [[ "$STATE" == 2 ]]; then
      printf '! %s: PID file huérfano (%s)\n' "$ROLE" "$PID_REASON"
      STALE_PID_FILES=$((STALE_PID_FILES + 1))
    else
      printf '· %s: sin PID gestionado; un servicio activo sería externo\n' "$ROLE"
    fi
  fi
done

printf '\nURL: %s\n' "$WEB_URL"
printf 'Logs: %s\n' "$LOG_DIR"

if ((STALE_PID_FILES > 0)); then
  FAILURES=$((FAILURES + STALE_PID_FILES))
fi
if ((FAILURES > 0)); then
  printf 'Estado incompleto: %s problema(s).\n' "$FAILURES"
  exit 1
fi
printf 'Estado completo: todos los servicios están activos.\n'
exit 0
