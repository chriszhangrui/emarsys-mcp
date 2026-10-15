#!/usr/bin/env bash
# run.sh — Helper script to run or test SAP Emarsys FastMCP Server

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PYTHON="$DIR/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "Creating virtualenv..."
    uv venv --python 3.12 .venv
    uv pip install --python "$PYTHON" -r requirements.txt
fi

case "$1" in
    check|test|--check|--test)
        "$PYTHON" server.py --check
        ;;
    list|--list-tools)
        "$PYTHON" server.py --list-tools
        ;;
    unittest)
        "$PYTHON" -m unittest test_emarsys.py -v
        ;;
    http)
        "$PYTHON" server.py --http --port "${PORT:-8000}"
        ;;
    *)
        exec "$PYTHON" server.py "$@"
        ;;
esac
