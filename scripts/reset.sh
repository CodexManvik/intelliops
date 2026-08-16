#!/usr/bin/env bash
# Reset the live stack to a clean slate WITHOUT a docker restart:
# recover the demo target, clear the detector baseline, empty the read model.
set -euo pipefail

DEMO=${DEMO_URL:-http://localhost:8080}
CORR=${CORR_URL:-http://localhost:8002}
READ=${READ_URL:-http://localhost:8007}

echo "→ Recovering demo-app…"
curl -fsS -X POST "$DEMO/fix" >/dev/null && echo "  demo-app healthy"
echo "→ Resetting correlation baseline…"
curl -fsS -X POST "$CORR/reset-baseline" >/dev/null && echo "  detector baseline cleared"
echo "→ Clearing read-model…"
curl -fsS -X POST "$READ/reset" >/dev/null && echo "  read model empty"
echo "✓ Clean slate. Next break will detect fresh."
