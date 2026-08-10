#!/usr/bin/env bash

# Shared, Bash 3.2-compatible helpers for the macOS launcher scripts.

if [[ -z "${PROJECT_ROOT:-}" ]]; then
  printf 'launcher-common.sh requiere PROJECT_ROOT.\n' >&2
  return 2
fi

promote_legacy_environment() {
  local canonical legacy
  canonical="$1"
  legacy="$2"
  if ! declare -p "$canonical" >/dev/null 2>&1 \
    && declare -p "$legacy" >/dev/null 2>&1; then
    export "$canonical=${!legacy}"
  fi
}

# LLC_* always wins. Legacy names remain a transition-only fallback for local
# launch configurations created before the product identity migration.
for LLC_SUFFIX in \
  APP_WRAPPER \
  EDUCATIONAL_LIBRARY_RUNTIME_DIR \
  EDUCATIONAL_MATERIALS_DIR \
  LAUNCHER_API_PORT \
  LAUNCHER_LMS_BIN \
  LAUNCHER_LM_STUDIO_BIN \
  LAUNCHER_LM_STUDIO_PORT \
  LAUNCHER_LOG_DIR \
  LAUNCHER_NO_ALERT \
  LAUNCHER_NO_OPEN \
  LAUNCHER_RUN_DIR \
  LAUNCHER_STOP_TIMEOUT \
  LAUNCHER_TEST_MODE \
  LAUNCHER_TIMEOUT \
  LAUNCHER_WEB_RUNTIME_ROOT \
  LAUNCHER_WEB_PORT \
  LM_APP_WILL_CLOSE \
  LM_STUDIO_READY
do
  promote_legacy_environment \
    "LLC_$LLC_SUFFIX" "DEUTSCHOS_$LLC_SUFFIX"
done
unset LLC_SUFFIX

export PATH="/opt/homebrew/bin:/usr/local/bin:$PROJECT_ROOT/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export NEXT_TELEMETRY_DISABLED=1
export DO_NOT_TRACK=1

LAUNCHER_TEST_MODE="${LLC_LAUNCHER_TEST_MODE:-0}"
if [[ "$LAUNCHER_TEST_MODE" == "1" ]]; then
  LM_STUDIO_PORT="${LLC_LAUNCHER_LM_STUDIO_PORT:-1234}"
  API_PORT="${LLC_LAUNCHER_API_PORT:-8000}"
  WEB_PORT="${LLC_LAUNCHER_WEB_PORT:-3000}"
  RUN_DIR="${LLC_LAUNCHER_RUN_DIR:-$PROJECT_ROOT/run}"
  LOG_DIR="${LLC_LAUNCHER_LOG_DIR:-$PROJECT_ROOT/logs}"
else
  LM_STUDIO_PORT=1234
  API_PORT=8000
  WEB_PORT=3000
  RUN_DIR="$PROJECT_ROOT/run"
  LOG_DIR="$PROJECT_ROOT/logs"
fi

PYTHON="$PROJECT_ROOT/.venv/bin/python"
NEXT_BIN="$PROJECT_ROOT/node_modules/.bin/next"
LM_STUDIO_URL="http://127.0.0.1:$LM_STUDIO_PORT"
API_URL="http://127.0.0.1:$API_PORT"
WEB_URL="http://127.0.0.1:$WEB_PORT"
WEB_RUNTIME_ROOT="${LLC_LAUNCHER_WEB_RUNTIME_ROOT:-$PROJECT_ROOT/var/desktop-web}"
WEB_RUNTIME_RELEASES="$WEB_RUNTIME_ROOT/releases"
LAUNCHER_LOG="$LOG_DIR/launcher.log"
STOP_TIMEOUT="${LLC_LAUNCHER_STOP_TIMEOUT:-5}"
LMS_BIN=""
LMS_BIN_DISPLAY=""
INSTALLATION_ID="$(printf '%s' "$PROJECT_ROOT" | shasum -a 256 | awk '{print $1}')"

resolve_web_runtime() {
  local release_name release_dir server_relative server_dir
  WEB_RUNTIME_RELEASE=""
  WEB_RUNTIME_SERVER_DIR=""
  WEB_RUNTIME_SERVER=""
  [[ -f "$WEB_RUNTIME_ROOT/current-release" && ! -L "$WEB_RUNTIME_ROOT/current-release" ]] \
    || return 1
  release_name="$(sed -n '1p' "$WEB_RUNTIME_ROOT/current-release" 2>/dev/null || true)"
  case "$release_name" in
    "" | .* | */*) return 1 ;;
  esac
  release_dir="$WEB_RUNTIME_RELEASES/$release_name"
  [[ -d "$release_dir" && ! -L "$release_dir" ]] || return 1
  server_relative="$(sed -n '1p' "$release_dir/server-relative" 2>/dev/null || true)"
  case "$server_relative" in
    .) server_dir="$release_dir" ;;
    apps/web) server_dir="$release_dir/apps/web" ;;
    *) return 1 ;;
  esac
  [[ -f "$server_dir/server.js" && ! -L "$server_dir/server.js" ]] || return 1
  WEB_RUNTIME_RELEASE="$release_dir"
  WEB_RUNTIME_SERVER_DIR="$server_dir"
  WEB_RUNTIME_SERVER="$server_dir/server.js"
  return 0
}

resolve_lms_bin() {
  local configured candidate
  configured="${LLC_LAUNCHER_LMS_BIN:-${LLC_LAUNCHER_LM_STUDIO_BIN:-}}"
  if [[ -n "$configured" ]]; then
    if [[ -x "$configured" && ! -d "$configured" ]]; then
      LMS_BIN="$configured"
      LMS_BIN_DISPLAY="$configured"
      case "$LMS_BIN_DISPLAY" in
        "$HOME"/*) LMS_BIN_DISPLAY="~/${LMS_BIN_DISPLAY#"$HOME"/}" ;;
      esac
      return 0
    fi
    return 1
  fi
  for candidate in \
    "$HOME/.lmstudio/bin/lms" \
    "/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms" \
    "$HOME/.local/bin/lms"
  do
    if [[ -x "$candidate" && ! -d "$candidate" ]]; then
      LMS_BIN="$candidate"
      LMS_BIN_DISPLAY="$candidate"
      case "$LMS_BIN_DISPLAY" in
        "$HOME"/*) LMS_BIN_DISPLAY="~/${LMS_BIN_DISPLAY#"$HOME"/}" ;;
      esac
      return 0
    fi
  done
  return 1
}

ensure_launcher_directories() {
  umask 077
  mkdir -p "$LOG_DIR" "$RUN_DIR"
  chmod 700 "$LOG_DIR" "$RUN_DIR" 2>/dev/null || true
}

launcher_log() {
  local message
  message="$1"
  ensure_launcher_directories
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$message" >>"$LAUNCHER_LOG"
  printf '%s\n' "$message"
}

show_macos_error() {
  local message
  message="$1"
  if [[ "${LLC_APP_WRAPPER:-0}" == "1" || "${LLC_LAUNCHER_NO_ALERT:-0}" == "1" ]]; then
    return 0
  fi
  if [[ -x /usr/bin/osascript ]]; then
    /usr/bin/osascript - "$message" >/dev/null 2>&1 <<'APPLESCRIPT' || true
on run argv
  display alert "LLC no pudo iniciarse" message (item 1 of argv) as critical
end run
APPLESCRIPT
  fi
}

fail_launcher() {
  local message
  message="$1"
  launcher_log "ERROR: $message" >&2
  show_macos_error "$message"
  exit 1
}

process_is_running() {
  local pid state
  pid="$1"
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 1
  fi
  state="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
  case "$state" in
    "" | Z*) return 1 ;;
    *) return 0 ;;
  esac
}

process_start_token() {
  # macOS renders lstart in the caller's timezone. Finder/AppleScript and an
  # interactive shell may therefore produce different text for the same PID.
  # Force both locale and timezone so the token is stable across invocations.
  LC_ALL=C TZ=UTC0 ps -o lstart= -p "$1" 2>/dev/null \
    | LC_ALL=C awk '{$1=$1; print}'
}

process_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1
}

expected_process() {
  local role pid command_line cwd
  role="$1"
  pid="$2"
  command_line="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  cwd="$(process_cwd "$pid")"
  case "$role" in
    api)
      [[ "$cwd" == "$PROJECT_ROOT" \
        && "$command_line" == *"uvicorn"*"llc_api.main:app"*"$PROJECT_ROOT/apps/api/src"* \
        && "$command_line" == *"--host 127.0.0.1"*"--port $API_PORT"* ]]
      ;;
    web)
      [[ "$cwd" == "$WEB_RUNTIME_RELEASES"/* \
        && ( "$command_line" == *"server.js"* || "$command_line" == next-server* ) ]]
      ;;
    *)
      return 1
      ;;
  esac
}

pid_file_path() {
  printf '%s/%s.pid\n' "$RUN_DIR" "$1"
}

write_pid_file() {
  local role pid ownership path started temporary
  role="$1"
  pid="$2"
  ownership="${3:-managed}"
  case "$ownership" in
    managed | adopted) ;;
    *) return 1 ;;
  esac
  path="$(pid_file_path "$role")"
  started="$(process_start_token "$pid")"
  [[ -n "$started" ]] || return 1
  temporary="$path.$$"
  (umask 077 && printf '%s\n%s\n%s\n' "$pid" "$started" "$ownership" >"$temporary") || {
    rm -f -- "$temporary"
    return 1
  }
  mv -f -- "$temporary" "$path"
  chmod 600 "$path" 2>/dev/null || true
}

# Return 0 for valid, 1 for absent, 2 for stale/invalid. Details are exported
# through PID_VALUE and PID_REASON without evaluating PID-file contents.
pid_file_state() {
  local role path recorded_start actual_start ownership
  role="$1"
  path="$(pid_file_path "$role")"
  PID_VALUE=""
  PID_REASON=""
  if [[ ! -f "$path" || -L "$path" ]]; then
    PID_REASON="ausente"
    return 1
  fi
  PID_VALUE="$(sed -n '1p' "$path" 2>/dev/null || true)"
  recorded_start="$(sed -n '2p' "$path" 2>/dev/null || true)"
  ownership="$(sed -n '3p' "$path" 2>/dev/null || true)"
  [[ -n "$ownership" ]] || ownership="managed"
  case "$PID_VALUE" in
    "" | *[!0-9]*)
      PID_REASON="contenido inválido"
      return 2
      ;;
  esac
  if ! process_is_running "$PID_VALUE"; then
    PID_REASON="proceso inexistente"
    return 2
  fi
  actual_start="$(process_start_token "$PID_VALUE")"
  if [[ -z "$recorded_start" || "$recorded_start" != "$actual_start" ]]; then
    PID_REASON="PID reutilizado o huella de inicio distinta"
    return 2
  fi
  if ! expected_process "$role" "$PID_VALUE"; then
    PID_REASON="el PID no pertenece al comando esperado"
    return 2
  fi
  case "$ownership" in
    managed | adopted) PID_OWNERSHIP="$ownership" ;;
    *)
      PID_REASON="propiedad inválida"
      return 2
      ;;
  esac
  PID_REASON="válido"
  return 0
}

clean_invalid_pid_file() {
  local role state
  role="$1"
  if pid_file_state "$role"; then
    return 0
  else
    state=$?
  fi
  if [[ "$state" == 2 ]]; then
    rm -f -- "$(pid_file_path "$role")"
  fi
  return 0
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
  return 2
}

role_port() {
  case "$1" in
    api) printf '%s\n' "$API_PORT" ;;
    web) printf '%s\n' "$WEB_PORT" ;;
    *) return 1 ;;
  esac
}

listener_pid_for_port() {
  local port pids count
  port="$1"
  command -v lsof >/dev/null 2>&1 || return 2
  pids="$(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -u)"
  [[ -n "$pids" ]] || return 1
  count="$(printf '%s\n' "$pids" | awk 'NF {count++} END {print count+0}')"
  [[ "$count" == "1" ]] || return 2
  printf '%s\n' "$pids"
}

lm_studio_ready() {
  local payload
  [[ -x "$PYTHON" ]] || return 1
  payload="$(
    curl --silent --fail --connect-timeout 1 --max-time 3 \
      "$LM_STUDIO_URL/v1/models" 2>/dev/null
  )" || return 1
  printf '%s' "$payload" | "$PYTHON" -c '
import json, sys
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
models = payload.get("data") if isinstance(payload, dict) else None
if not isinstance(models, list):
    raise SystemExit(1)
if not all(
    isinstance(item, dict)
    and isinstance(item.get("id"), str)
    and bool(item["id"].strip())
    for item in models
):
    raise SystemExit(1)
' >/dev/null 2>&1
}

api_ready() {
  local response
  response="$(curl --silent --fail --connect-timeout 1 --max-time 3 "$API_URL/health" 2>/dev/null)" || return 1
  [[ "$response" == *'"status":"ok"'* && "$response" == *'"service":"llc-api"'* ]]
}

web_ready() {
  local response
  response="$(curl --silent --fail --connect-timeout 1 --max-time 5 "$WEB_URL/api/health" 2>/dev/null)" || return 1
  [[ "$response" == *'"status":"ok"'* \
    && "$response" == *'"service":"llc-web"'* \
    && "$response" == *'"runtime":"desktop-production"'* \
    && "$response" == *"\"installation_id\":\"$INSTALLATION_ID\""* ]]
}

role_ready() {
  case "$1" in
    api) api_ready ;;
    web) web_ready ;;
    *) return 1 ;;
  esac
}

# Export SERVICE_STATE, SERVICE_PID and SERVICE_REASON. Status checks may adopt
# a listener only after port, HTTP identity, command, cwd and PID all agree.
classify_role() {
  local role pid_state port listener
  role="$1"
  SERVICE_STATE=""
  SERVICE_PID=""
  SERVICE_REASON=""
  if pid_file_state "$role"; then
    SERVICE_STATE="$PID_OWNERSHIP"
    SERVICE_PID="$PID_VALUE"
    SERVICE_REASON="PID y huella de inicio válidos"
    return 0
  else
    pid_state=$?
  fi
  port="$(role_port "$role")" || return 2
  if port_is_busy "$port"; then
    listener="$(listener_pid_for_port "$port" 2>/dev/null || true)"
    if [[ -n "$listener" ]] && role_ready "$role" && expected_process "$role" "$listener"; then
      if write_pid_file "$role" "$listener" adopted; then
        SERVICE_STATE="adopted"
        SERVICE_PID="$listener"
        SERVICE_REASON="listener LLC exacto recuperado"
        return 0
      fi
      SERVICE_STATE="external"
      SERVICE_PID="$listener"
      SERVICE_REASON="la identidad coincide, pero no se pudo persistir la adopción"
      return 0
    fi
    SERVICE_STATE="external"
    SERVICE_PID="$listener"
    SERVICE_REASON="el listener del puerto $port no pudo verificarse como LLC"
    return 0
  fi
  if [[ "$pid_state" == 2 ]]; then
    SERVICE_STATE="stale"
    SERVICE_REASON="$PID_REASON"
  else
    SERVICE_STATE="absent"
    SERVICE_REASON="sin listener ni registro"
  fi
  return 0
}

active_model_names() {
  local payload
  [[ -x "$PYTHON" ]] || return 1
  payload="$(
    curl --silent --fail --connect-timeout 1 --max-time 3 "$API_URL/api/models" 2>/dev/null
  )" || payload="$(
    curl --silent --fail --connect-timeout 1 --max-time 3 "$LM_STUDIO_URL/v1/models" 2>/dev/null
  )" || return 1
  printf '%s' "$payload" | "$PYTHON" -c '
import json, sys
payload = json.load(sys.stdin)
models = payload.get("models")
name_key = "name"
if not isinstance(models, list):
    models = payload.get("data", [])
    name_key = "id"
names = []
for item in models if isinstance(models, list) else []:
    if isinstance(item, dict):
        name = item.get(name_key)
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
print(", ".join(names))
' 2>/dev/null
}

wait_for_probe() {
  local label probe pid timeout attempt
  label="$1"
  probe="$2"
  pid="$3"
  timeout="$4"
  attempt=0
  while ((attempt < timeout)); do
    if "$probe"; then
      launcher_log "$label listo."
      return 0
    fi
    if [[ -n "$pid" ]] && ! process_is_running "$pid"; then
      launcher_log "$label terminó antes de estar listo."
      return 1
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  launcher_log "$label no respondió antes del timeout (${timeout}s)."
  return 1
}

terminate_tree_signal() {
  local pid signal child
  pid="$1"
  signal="$2"
  if command -v pgrep >/dev/null 2>&1; then
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
      terminate_tree_signal "$child" "$signal"
    done
  fi
  if process_is_running "$pid"; then
    kill -"$signal" "$pid" >/dev/null 2>&1 || true
  fi
}

stop_validated_role() {
  local role pid attempt
  role="$1"
  if ! pid_file_state "$role"; then
    return 1
  fi
  pid="$PID_VALUE"
  launcher_log "Deteniendo $role gestionado (PID $pid) con SIGTERM..."
  terminate_tree_signal "$pid" TERM
  attempt=0
  while ((attempt < 15)); do
    if ! process_is_running "$pid"; then
      rm -f -- "$(pid_file_path "$role")"
      launcher_log "$role detenido limpiamente."
      return 0
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  launcher_log "$role no terminó tras 15s; usando SIGKILL como último recurso."
  terminate_tree_signal "$pid" KILL
  attempt=0
  while ((attempt < 5)) && process_is_running "$pid"; do
    sleep 1
    attempt=$((attempt + 1))
  done
  rm -f -- "$(pid_file_path "$role")"
  ! process_is_running "$pid"
}

lm_studio_listener_is_exact() {
  local pid command_line
  pid="$(listener_pid_for_port "$LM_STUDIO_PORT" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  command_line="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  [[ "$command_line" == *"/Applications/LM Studio.app/Contents/MacOS/LM Studio"* ]]
}

stop_exact_lm_studio_listener() {
  local pid attempt
  lm_studio_listener_is_exact || return 1
  pid="$(listener_pid_for_port "$LM_STUDIO_PORT")" || return 1
  launcher_log "LM Studio conserva un listener exacto (PID $pid); aplicando cierre acotado."
  terminate_tree_signal "$pid" TERM
  attempt=0
  while ((attempt < 8)) && process_is_running "$pid"; do
    sleep 1
    attempt=$((attempt + 1))
  done
  if process_is_running "$pid"; then
    launcher_log "El listener exacto de LM Studio no respondió; usando SIGKILL focalizado."
    terminate_tree_signal "$pid" KILL
  fi
  wait_for_port_free "LM Studio" "$LM_STUDIO_PORT" 5
}

wait_for_port_free() {
  local label port timeout attempt state
  label="$1"
  port="$2"
  timeout="$3"
  attempt=0
  while ((attempt < timeout)); do
    port_is_busy "$port"
    state=$?
    if [[ "$state" == 1 ]]; then
      launcher_log "$label: puerto $port libre."
      return 0
    fi
    if [[ "$state" == 2 ]]; then
      launcher_log "ERROR: no se puede comprobar el puerto $port de $label."
      return 1
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  launcher_log "ERROR: timeout; $label conserva el listener 127.0.0.1:$port."
  return 1
}
