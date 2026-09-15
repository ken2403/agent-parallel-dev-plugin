#!/usr/bin/env bash
set -euo pipefail
CA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONDONTWRITEBYTECODE=1 python3 "$CA_ROOT/tests/support/peer-review-test.py"
