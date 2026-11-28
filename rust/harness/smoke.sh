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

compose up -d --build --wait --wait-timeout 600 ingestion correlation feedback read governance rca action   demo-app meridian-gateway meridian-validation meridian-aggregation meridian-reporting
[ "$(docker inspect -f '{{.Config.Image}}' "$(compose ps -q ingestion)")" = intelliops-ingestion-rs:latest ] ||
  fail "ingestion is not the Rust image"
[ "$(docker inspect -f '{{.Config.User}}' "$(compose ps -q ingestion)")" = 10001 ] ||
  fail "ingestion does not run unprivileged"
echo " ✓ Rust ingestion is healthy and unprivileged"

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
marker=smoke-$$-$RANDOM
body='{"events":[{"source":"'$marker'","kind":"metric","name":"smoke_metric","value":1,"labels":{"service":"smoke"}}]}'
[ "$(curl -fsS -H 'Content-Type: application/json' -d "$body" http://localhost:8001/ingest)" = '{"accepted":1}' ] ||
  fail "/ingest did not accept the batch"
# Captured first: `grep -q` quitting early would fail the pipe under pipefail.
recent=$(compose exec -T redis redis-cli XREVRANGE telemetry.raw + - COUNT 2000)
grep -q "$marker" <<<"$recent" || fail "nothing reached telemetry.raw"
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

curl -fsS http://localhost:8007/metrics | grep -q '"situationsOpen"' || fail "read /metrics"
curl -fsS http://localhost:8007/system | grep -q '"llm"' || fail "read /system"
curl -fsS http://localhost:8006/metrics | grep -q '"total_outcomes"' || fail "feedback /metrics"
echo " ✓ Rust read and feedback answer"

curl -fsS http://localhost:8005/playbooks | grep -q restart-pod || fail "governance /playbooks"
code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json'   -d '{"id":"x","name":"x","match_rule":"x","hitl_mode":"auto"}'   'http://localhost:8005/playbooks?registered_by=nobody')
[ "$code" = 403 ] || fail "an unknown actor registered a playbook ($code)"
echo " ✓ Rust governance serves playbooks and refuses strangers"

curl -fsS http://localhost:8003/config/llm | grep -q '"provider"' || fail "rca /config/llm"
echo " ✓ Rust rca is serving"
curl -fsS http://localhost:8004/config/posture | grep -q '"remediator_mode"' || fail "action posture"
echo " ✓ Rust action is serving"
curl -fsS http://localhost:8080/metrics | grep -q '^cpu_usage ' || fail "demo-app metrics"
curl -fsS http://localhost:8008/ | grep -qi '<html' || fail "Meridian UI"
curl -fsS http://localhost:8011/metrics | grep -q '^service_up 1' || fail "meridian metrics"
echo " ✓ Rust demo app and Meridian are serving"
