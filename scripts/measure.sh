#!/usr/bin/env bash
# Image size, memory, ingest throughput and cold start of the running stack.
# Prereq: `docker compose -f deploy/docker-compose.yml up -d --wait`.
#
# The published Python-versus-Rust figures (docs/rust/baselines) were taken
# with an equivalent harness while both stacks existed.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() {
  docker compose -f deploy/docker-compose.yml "$@"
}
SERVICES="ingestion correlation rca action governance feedback read"
WORKLOADS="demo-app meridian-gateway meridian-validation meridian-aggregation meridian-reporting"
BATCHES=${BATCHES:-100}
BATCH_SIZE=200

now_ms() {
  date +%s%3N
}

echo "== image size (MiB) and memory"
for service in $SERVICES $WORKLOADS; do
  id=$(compose ps -q "$service")
  size=$(docker image inspect -f '{{.Size}}' "$(docker inspect -f '{{.Image}}' "$id")")
  memory=$(docker stats --no-stream --format '{{.MemUsage}}' "$id" | cut -d/ -f1)
  printf '%-22s %6s MiB image   %s\n' "$service" "$((size / 1048576))" "$memory"
done

echo "== ingest ($((BATCHES * BATCH_SIZE)) events)"
batch=$(mktemp)
trap 'rm -f "$batch"' EXIT
{
  printf '{"events":['
  for i in $(seq 0 $((BATCH_SIZE - 1))); do
    [ "$i" -gt 0 ] && printf ','
    printf '{"source":"bench","kind":"metric","name":"bench_metric_%d","value":1,"labels":{"service":"svc-%d"}}' \
      $((i % 20)) $((i % 10))
  done
  printf ']}'
} >"$batch"
start=$(now_ms)
for _ in $(seq "$BATCHES"); do
  curl -fsS -o /dev/null -H 'Content-Type: application/json' --data-binary "@$batch" http://localhost:8001/ingest
done
accepted=$(($(now_ms) - start))
echo "accepted: $((BATCHES * BATCH_SIZE * 1000 / accepted)) events/s"

echo "== cold start to ready (s)"
port=8001
for service in $SERVICES; do
  compose stop "$service" >/dev/null 2>&1
  start=$(now_ms)
  compose start "$service" >/dev/null 2>&1
  until curl -fsS -o /dev/null --max-time 2 "http://localhost:$port/ready" 2>/dev/null; do :; done
  elapsed=$(($(now_ms) - start))
  printf '%-22s %d.%03d\n' "$service" $((elapsed / 1000)) $((elapsed % 1000))
  port=$((port + 1))
done
