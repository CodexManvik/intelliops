#!/usr/bin/env bash
# load-test.sh — Drive synthetic incidents through the ingestion endpoint.
#
# Usage:
#   ./scripts/load-test.sh [incidents_per_minute] [duration_seconds]
#
# Arguments:
#   incidents_per_minute  Rate of requests (default: 60)
#   duration_seconds      How long to run the test (default: 60)
#                         Set to 0 to exit immediately.
#
# Prerequisites:
#   - The IntelliOps stack must be running (docker compose up or k8s)
#   - INGEST_URL env var can override the default http://localhost:8000/ingest
#
# Tools used: curl, date, awk, sort, bc (all POSIX)
#
# Results are printed to stdout and appended to docs/OPERATIONS.md.

set -euo pipefail

INCIDENTS_PER_MINUTE="${1:-60}"
DURATION_SECONDS="${2:-60}"
INGEST_URL="${INGEST_URL:-http://localhost:8000/ingest}"
OPERATIONS_MD="$(dirname "$0")/../docs/OPERATIONS.md"

# ── Handle duration=0 edge case ──────────────────────────────────────────────
if [ "$DURATION_SECONDS" -eq 0 ]; then
  echo "Total events: 0"
  echo "p50 latency: 0 ms"
  echo "p95 latency: 0 ms"
  exit 0
fi

# ── Validate numeric args ─────────────────────────────────────────────────────
if ! echo "$INCIDENTS_PER_MINUTE" | grep -qE '^[0-9]+$'; then
  echo "Error: incidents_per_minute must be a non-negative integer, got: $INCIDENTS_PER_MINUTE" >&2
  exit 1
fi
if ! echo "$DURATION_SECONDS" | grep -qE '^[0-9]+$'; then
  echo "Error: duration_seconds must be a non-negative integer, got: $DURATION_SECONDS" >&2
  exit 1
fi

# ── Compute inter-request sleep interval ─────────────────────────────────────
# interval_ms = 60000 / incidents_per_minute  (milliseconds between requests)
# If rate is 0, we'd divide by zero — treat 0 rpm as "no sending, just exit"
if [ "$INCIDENTS_PER_MINUTE" -eq 0 ]; then
  echo "Total events: 0"
  echo "p50 latency: 0 ms"
  echo "p95 latency: 0 ms"
  exit 0
fi

INTERVAL_MS=$(echo "scale=6; 60000 / $INCIDENTS_PER_MINUTE" | bc)
INTERVAL_S=$(echo "scale=6; 60 / $INCIDENTS_PER_MINUTE" | bc)

# ── Temporary file for latency samples ───────────────────────────────────────
LATENCY_FILE=$(mktemp)
trap 'rm -f "$LATENCY_FILE"' EXIT

total_sent=0
start_epoch=$(date +%s)
end_epoch=$(( start_epoch + DURATION_SECONDS ))

echo "Starting load test: ${INCIDENTS_PER_MINUTE} req/min for ${DURATION_SECONDS}s → ${INGEST_URL}"

# ── Main request loop ─────────────────────────────────────────────────────────
while true; do
  now_epoch=$(date +%s)
  if [ "$now_epoch" -ge "$end_epoch" ]; then
    break
  fi

  # ISO-8601 UTC timestamp for the event
  TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  PAYLOAD=$(printf '{"events": [{"source": "load-test", "kind": "metric", "name": "cpu_usage", "value": 0.75, "labels": {}, "ts": "%s"}]}' "$TS")

  # Record start time in nanoseconds if available, otherwise seconds
  req_start=$(date +%s%N 2>/dev/null || date +%s)

  # POST the payload; capture HTTP status code; suppress body output
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "$INGEST_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    --max-time 10 \
    2>/dev/null || echo "000")

  req_end=$(date +%s%N 2>/dev/null || date +%s)

  # Compute latency in milliseconds
  # If date +%s%N is available (nanosecond precision), compute properly
  if echo "$req_start" | grep -qE '^[0-9]{13,}$'; then
    # nanosecond timestamps available
    latency_ms=$(( (req_end - req_start) / 1000000 ))
  else
    # second-precision fallback: treat as 0 ms (not ideal but POSIX-safe)
    latency_ms=0
  fi

  echo "$latency_ms" >> "$LATENCY_FILE"
  total_sent=$(( total_sent + 1 ))

  # Sleep until the next scheduled request
  sleep "$INTERVAL_S" 2>/dev/null || true
done

# ── Compute percentiles with awk ──────────────────────────────────────────────
if [ "$total_sent" -eq 0 ]; then
  p50=0
  p95=0
else
  # Sort numerically, then use awk to pick the p50 and p95 values
  SORTED_FILE=$(mktemp)
  trap 'rm -f "$LATENCY_FILE" "$SORTED_FILE"' EXIT
  sort -n "$LATENCY_FILE" > "$SORTED_FILE"

  read -r p50 p95 <<EOF
$(awk -v count="$total_sent" '
  BEGIN { p50_idx = int(count * 0.50); p95_idx = int(count * 0.95);
          if (p50_idx < 1) p50_idx = 1;
          if (p95_idx < 1) p95_idx = 1; }
  NR == p50_idx { p50 = $1 }
  NR == p95_idx { p95 = $1 }
  END { print p50 " " p95 }
' "$SORTED_FILE")
EOF
fi

# ── Print results to stdout ───────────────────────────────────────────────────
echo ""
echo "Total events: ${total_sent}"
echo "p50 latency: ${p50} ms"
echo "p95 latency: ${p95} ms"

# ── Append results block to OPERATIONS.md ────────────────────────────────────
RUN_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [ -w "$OPERATIONS_MD" ]; then
  cat >> "$OPERATIONS_MD" <<RESULTS_BLOCK

## Load test results — ${RUN_TS}

| Metric | Value |
| --- | --- |
| Date/time (UTC) | ${RUN_TS} |
| Rate (incidents/min) | ${INCIDENTS_PER_MINUTE} |
| Duration (seconds) | ${DURATION_SECONDS} |
| Total events sent | ${total_sent} |
| p50 latency | ${p50} ms |
| p95 latency | ${p95} ms |

RESULTS_BLOCK
  echo "Results appended to $OPERATIONS_MD"
else
  echo "Warning: $OPERATIONS_MD is not writable — results not persisted." >&2
fi
