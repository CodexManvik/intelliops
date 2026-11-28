#!/usr/bin/env bash
# Check a running compose stack end to end.
# Prereq: `docker compose -f deploy/docker-compose.yml up -d --wait`.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() {
  docker compose -f deploy/docker-compose.yml "$@"
}
fail() {
  echo " ✗ $1"
  exit 1
}
get() {
  curl -fsS --retry 10 --retry-delay 2 --retry-all-errors "$1"
}

for port in 8001 8002 8003 8004 8005 8006 8007; do
  get "http://localhost:$port/ready" >/dev/null || fail "port $port is not ready"
done
echo " ✓ all seven platform services are ready"

for service in ingestion action meridian-gateway; do
  user=$(docker inspect -f '{{.Config.User}}' "$(compose ps -q "$service")")
  [ "$user" = 10001 ] || fail "$service runs as '$user', not unprivileged"
done
echo " ✓ images run unprivileged"

since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
marker=smoke-$$-$RANDOM
body='{"events":[{"source":"'$marker'","kind":"metric","name":"smoke_metric","value":1,"labels":{"service":"smoke"}}]}'
[ "$(curl -fsS -H 'Content-Type: application/json' -d "$body" http://localhost:8001/ingest)" = '{"accepted":1}' ] ||
  fail "/ingest did not accept the batch"
# The stream is capped, so its length stops growing: look for our own event.
# Captured first: `grep -q` quitting early would fail the pipe under pipefail.
recent=$(compose exec -T redis redis-cli XREVRANGE telemetry.raw + - COUNT 2000)
grep -q "$marker" <<<"$recent" || fail "nothing reached telemetry.raw"
code=$(curl -s -o /dev/null -w '%{http_code}' -d '{"events":[{}]}' http://localhost:8001/ingest)
[ "$code" = 422 ] || fail "a bad event answered $code, not 422"
echo " ✓ /ingest publishes and refuses bad input"

sleep 5
groups=$(compose exec -T redis redis-cli --json XINFO GROUPS telemetry.raw)
lag=$(grep -oE '"name":"correlation"[^}]*"lag":[0-9]+' <<<"$groups" | grep -oE '[0-9]+$')
[ "$lag" -lt 100 ] || fail "correlation is $lag events behind"
errors=$(compose logs --since "$since" correlation)
if grep -E 'undecodable|panicked|"level":"ERROR"' <<<"$errors"; then
  fail "correlation reported errors"
fi
echo " ✓ correlation is consuming"

page() {
  local body
  body=$(get "$1") || fail "$1 failed"
  grep -qi -- "$2" <<<"$body" || fail "$1 did not contain $2"
}
page http://localhost:8007/metrics '"situationsOpen"'
page http://localhost:8007/system '"llm"'
page http://localhost:8007/_metrics intelliops_http_requests_total
page http://localhost:8006/metrics '"total_outcomes"'
page http://localhost:8005/playbooks restart-pod
page http://localhost:8003/config/llm '"provider"'
page http://localhost:8004/config/posture '"remediator_mode"'
page http://localhost:8080/metrics cpu_usage
page http://localhost:8008/ '<html'
page http://localhost:8011/metrics 'service_up 1'
echo " ✓ every service and workload answers"

code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d '{"id":"x","name":"x","match_rule":"x","hitl_mode":"auto"}' \
  'http://localhost:8005/playbooks?registered_by=nobody')
[ "$code" = 403 ] || fail "an unknown actor registered a playbook ($code)"
echo " ✓ governance refuses strangers"
