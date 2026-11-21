#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
if ! .venv/bin/python -c "import geotrace.app; import PySide6.QtWebEngineQuick" >/dev/null 2>&1; then
    .venv/bin/python -m pip install -e .
fi
exec .venv/bin/python -m geotrace.app "$@"
