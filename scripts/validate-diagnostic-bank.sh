#!/usr/bin/env bash

set -u
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  printf 'Falta el entorno Python 3.12 en %s\n' "$PYTHON" >&2
  exit 2
fi

exec "$PYTHON" "$ROOT/scripts/validate-diagnostic-bank.py" "$@"
