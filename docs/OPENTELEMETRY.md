# Observing an OpenTelemetry-instrumented system

Meridian exists to give IntelliOps something to watch, but it is a system we
wrote, with faults we designed to be detectable — which is exactly the
credibility gap it cannot close on its own. This is the path to pointing
IntelliOps at a system it did **not** author.

## The shape

IntelliOps' ingestion service speaks **PromQL**, not OTLP ([ADR-002](../architectural.md)).
Rather than teach every service a second telemetry protocol, the integration
adds one adapter:

```
  any OTel-instrumented app  --OTLP-->  collector  --/metrics-->  Prometheus
                                                                      |
                                                        ingestion polls PromQL
                                                                      |
                                              correlation -> rca -> governance -> action
```

Nothing downstream of Prometheus changes. The correlator, RCA, the gates and the
remediation path see OTel-sourced metrics exactly as they see Meridian's.

## Running it

The collector is an **opt-in overlay** — the base stack does not include it:

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.otel.yml up -d
```

That adds:

| | |
|---|---|
| `otel-collector` | OTLP ingress on **:4317** (gRPC) and **:4318** (HTTP), re-publishing on **:8889** |
| `prometheus` | gains an `otel-collector` scrape job (`deploy/prometheus.otel.yml`) |
| `ingestion` | its PromQL selector widened to the OTel families |

Point any instrumented app at it:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318   # or otel-collector:4318 in-network
```

To observe the [OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo),
run it on the same Docker network with that endpoint — its ~20 services then
appear in IntelliOps as ordinary incidents.

## The two pieces of glue that actually matter

**1. `service.name` becomes the `service` label.** RCA attributes an incident by
a `service` label, and the correlator groups by it under
`INTELLIOPS_CORRELATION_GROUP_BY=service`. OTel carries the name as a *resource
attribute*, so the collector promotes it
(`deploy/otel-collector-config.yml`, the `transform/service_label` processor).
Without this every OTel incident would arrive unattributed and be blamed on
`unknown`.

**2. OTel says `duration` where Meridian says `latency`.** The semantic
conventions use `http.server.request.duration` and `rpc.server.duration`.
`rank_hypotheses`' latency rule therefore matches `duration` as well as
`latency`; without that token every OTel latency incident would match no rule and
**escalate** — honest, but useless.

Verified mapping (probe `rank_hypotheses` directly):

```
  otel_http_server_request_duration_ms -> scale-service   (0.55)
  otel_process_cpu_utilization         -> scale-service   (0.60)
  otel_process_memory_usage_mb         -> restart-pod     (0.65)
  otel_http_server_error_rate          -> restart-pod     (0.58)
  otel_queue_depth                     -> scale-service   (0.55)
```

## Verifying the ingress

```bash
# emit a metric as any OTel SDK would
curl -X POST localhost:4318/v1/metrics -H 'Content-Type: application/json' -d '{
  "resourceMetrics":[{"resource":{"attributes":[
    {"key":"service.name","value":{"stringValue":"checkout-service"}}]},
    "scopeMetrics":[{"metrics":[{"name":"otel_http_server_request_duration_ms",
      "gauge":{"dataPoints":[{"asDouble":420,"timeUnixNano":"'$(date +%s)'000000000"}]}}]}]}]}'

# the collector re-publishes it, with `service` promoted
curl -s localhost:8889/metrics | grep otel_

# and Prometheus has it
curl -s 'localhost:9090/api/v1/query?query=otel_http_server_request_duration_ms'
```

## Honest limits

- **Metrics only.** The collector's pipeline handles the metrics signal. OTel
  traces and logs are received but not forwarded anywhere IntelliOps reads —
  correlation is metric-driven today.
- **Remediation still needs Kubernetes.** Detection, diagnosis and the governance
  gates work against anything that reports metrics, but `scale-service` /
  `restart-pod` / `rollback-deploy` act on Kubernetes Deployments
  ([ADR-013](../architectural.md)). Observing a non-k8s system gets you the loop
  up to the gate; remediating it needs a matching remediator.
- **Metric names are a convention, not a contract.** The RCA rules key on
  name *tokens* (`cpu`, `memory`, `duration`, `error`, `queue`). An app whose
  metrics are named outside those families will be detected and correlated, then
  escalate as "root cause undetermined" — which is the designed behaviour, not a
  failure, but it means adopting a new system may mean adding a rule.
