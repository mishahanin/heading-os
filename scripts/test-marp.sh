#!/usr/bin/env bash
# Run MARP test suite: unit tests + self-test
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== MARP Test Suite ==="
echo ""

echo "--- Unit Tests (pytest) ---"
cd "$WORKSPACE_ROOT"
python3 -m pytest tests/test_marp_render.py tests/test_marp_integration.py -v --tb=short
echo ""

echo "--- Self-Test (render sample deck) ---"
python3 scripts/marp_render.py --self-test
echo ""

echo "=== All MARP tests complete ==="
