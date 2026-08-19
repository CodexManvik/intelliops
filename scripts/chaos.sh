#!/usr/bin/env bash
# Drive one full incident through the live stack.
# Prereq: `docker compose -f deploy/docker-compose.yml up` is running.
set -euo pipefail

DEMO=${DEMO_URL:-http://localhost:8080}
READ=${READ_URL:-http://localhost:8007}

if [ "${1:-}" = "reset" ]; then
  exec "$(dirname "$0")/reset.sh"
fi

echo "→ Resetting to a clean slate first…"
"$(dirname "$0")/reset.sh" >/dev/null 2>&1 || true

echo "→ Breaking demo-app (error rate + CPU spike)…"
curl -fsS -X POST "$DEMO/break" >/dev/null
echo "  broken. Generating error traffic…"
for _ in $(seq 1 20); do curl -fsS "$DEMO/work" >/dev/null 2>&1 || true; done

echo "→ Waiting ~30s for detect → diagnose (Prometheus scrape + poll + anomaly)…"
sleep 30

echo "→ Current situations (read model):"
curl -fsS "$READ/situations" | python -m json.tool || true

echo
echo "Now open the console (http://localhost:5173 with VITE_DATA_MODE=live) and"
echo "click Approve on the open situation to remediate it (dry-run)."
echo "When done, recover the app:  curl -X POST $DEMO/fix"
