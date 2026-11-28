#!/usr/bin/env bash
# Swap the Rust services into a running Python stack and check they interoperate.
# Prereq: `docker compose -f deploy/docker-compose.yml up -d` is running.
set -euo pipefail
cd "$(dirname "$0")/../.."

compose() {
  docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.rust.yml "$@"
}
fail() {
  echo " ✗ $1"
  exit 1
}
streamlen() {
  compose exec -T redis redis-cli XLEN telemetry.raw | tr -d '\r'
}

compose up -d --build --wait --wait-timeout 180 ingestion correlation
[ "$(docker inspect -f '{{.Config.Image}}' "$(compose ps -q ingestion)")" = intelliops-ingestion-rs:latest ] ||
  fail "ingestion is not the Rust image"
[ "$(docker inspect -f '{{.Config.User}}' "$(compose ps -q ingestion)")" = 10001 ] ||
  fail "ingestion does not run unprivileged"
echo " ✓ Rust ingestion is healthy and unprivileged"

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
before=$(streamlen)
body='{"events":[{"source":"smoke","kind":"metric","name":"smoke_metric","value":1,"labels":{"service":"smoke"}}]}'
[ "$(curl -fsS -H 'Content-Type: application/json' -d "$body" http://localhost:8001/ingest)" = '{"accepted":1}' ] ||
  fail "/ingest did not accept the batch"
[ "$(streamlen)" -gt "$before" ] || fail "nothing reached telemetry.raw"
code=$(curl -s -o /dev/null -w '%{http_code}' -d '{"events":[{}]}' http://localhost:8001/ingest)
[ "$code" = 422 ] || fail "a bad event answered $code, not 422"
echo " ✓ /ingest publishes and refuses bad input"

sleep 5
lag=$(compose exec -T redis redis-cli --json XINFO GROUPS telemetry.raw |
  grep -oE '"name":"correlation"[^}]*"lag":[0-9]+' | grep -oE '[0-9]+$')
[ "$lag" -lt 100 ] || fail "correlation is $lag events behind"
if compose logs --since "$since" correlation | grep -E 'undecodable|panicked|"level":"ERROR"'; then
  fail "correlation reported errors"
fi
echo " ✓ Rust correlation consumes Rust events"
