#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
JUSTBUILD_VENV="${JUSTBUILD_VENV:-$HOME/.cache/justbuild/venv}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$JUSTBUILD_VENV/bin/python" -m justbuild serve "$@"

