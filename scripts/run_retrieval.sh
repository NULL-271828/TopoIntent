#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHASHSEED=0
export PYTHONUTF8=1
exec .venv/bin/python scripts/run_retrieval.py "$@"
