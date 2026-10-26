#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
JUSTBUILD_VENV="${JUSTBUILD_VENV:-$HOME/.cache/justbuild/venv}"
if [ ! -x "$JUSTBUILD_VENV/bin/python" ]; then
  python3 -m venv "$JUSTBUILD_VENV"
fi
"$JUSTBUILD_VENV/bin/python" -m pip install --timeout 120 -e '.[test,lan]'
printf '\nReady. Run: ./scripts/run.sh\n'

