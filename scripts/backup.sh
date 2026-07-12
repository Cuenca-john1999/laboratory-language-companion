#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.12)"
else
  printf 'Error: se necesita Python 3.12 para crear el backup.\n' >&2
  exit 1
fi

exec "$PYTHON" "$ROOT/scripts/backup_bundle.py" --root "$ROOT" "$@"
