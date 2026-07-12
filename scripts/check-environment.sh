#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
printf 'Aviso: check-environment.sh está obsoleto; usa scripts/doctor.sh.\n' >&2
exec "$ROOT/scripts/doctor.sh" "$@"
