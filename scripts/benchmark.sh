#!/usr/bin/env bash
# Thin wrapper around the correlator benchmark harness.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
uv run python scripts/benchmark.py
