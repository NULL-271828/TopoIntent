#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
exec ./scripts/run_precomputed_verification.sh --claim stage5_validation "$@"
