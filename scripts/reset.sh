#!/usr/bin/env bash
# Reset the live stack to a clean slate WITHOUT a docker restart: recover the
# demo target, clear the detector baseline, empty the read model, and drop any
# pending approvals. The audit trail and training records are deliberately
# preserved (durable compliance/learning record).
#
# Under AUTH_MODE=token the reset endpoints are gated like everything else — set
# AUTH_TOKEN to the shared token and it is sent as a bearer header.
set -euo pipefail

DEMO=${DEMO_URL:-http://localhost:8080}
CORR=${CORR_URL:-http://localhost:8002}
READ=${READ_URL:-http://localhost:8007}
GOV=${GOV_URL:-http://localhost:8005}

# Attach the bearer token when AUTH_TOKEN is set (token mode); no header otherwise.
AUTH_ARGS=()
if [[ -n "${AUTH_TOKEN:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${AUTH_TOKEN}")
fi

echo "→ Recovering demo-app…"
curl -fsS -X POST "$DEMO/fix" >/dev/null && echo "  demo-app healthy"
echo "→ Resetting correlation baseline…"
curl -fsS "${AUTH_ARGS[@]}" -X POST "$CORR/reset-baseline" >/dev/null && echo "  detector baseline cleared"
echo "→ Clearing read-model…"
curl -fsS "${AUTH_ARGS[@]}" -X POST "$READ/reset" >/dev/null && echo "  read model empty"
echo "→ Clearing pending approvals…"
curl -fsS "${AUTH_ARGS[@]}" -X POST "$GOV/reset-approvals" >/dev/null && echo "  approvals cleared"
echo "✓ Clean slate (audit trail + training records preserved). Next break will detect fresh."
