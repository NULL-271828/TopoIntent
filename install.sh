#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check 'torch==2.7.1' --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-ae.txt
printf '%s\n' 'Installed CPU AE environment. No model downloaded. Next: ./scripts/smoke_test.sh'
