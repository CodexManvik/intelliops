# Meridian — a sample production system for IntelliOps to operate

Meridian is a small, realistic **Deloitte-style financial/audit reporting platform**: four
backend services plus a client portal, running in the same `docker compose` stack as IntelliOps.
It exists for one reason — to give IntelliOps something **real** to watch. Its services emit
genuine Prometheus metrics, its faults are genuine step-changes (not canned incidents), and when
you break it, IntelliOps genuinely **detects → diagnoses → gates → remediates** the incident, the
same closed loop documented in [flow.md](../flow.md) and [architectural.md](../architectural.md).

Read this alongside [ADR-020](../architectural.md#adr-020--meridian-sample-production-system) for
*why* it's built this way, and the design spec at
[docs/superpowers/specs/2026-08-25-meridian-sample-system-design.md](superpowers/specs/2026-08-25-meridian-sample-system-design.md)
for the full original design.

---

## 1. What Meridian is

A client submits financial data, it gets validated, aggregated into roll-ups, and turned into a
report — the kind of multi-service platform a managed-services provider (the Deloitte framing)
would operate on a client's behalf, with IntelliOps as the AIOps layer keeping it healthy.

| Service | Domain role | Real endpoints | Fault it showcases |
|---|---|---|---|
| `meridian-gateway` | API front door + serves the portal UI | `POST /api/submissions`, `GET /api/reports`, ops proxy, `/admin/deploy` | **rollback-deploy** |
| `meridian-validation` | validates submitted financial data | (fault/metrics scaffold; a real validate route is a natural next step) | **restart-pod** |
| `meridian-aggregation` | roll-ups / heavy compute | (fault/metrics scaffold) | **scale-service** |
| `meridian-reporting` | report generation | (fault/metrics scaffold) | **scale-service** |

All four are built from one shared factory, `make_meridian_service()` in
`services/meridian/common.py` — the same pattern `services/demo_app` established: `create_app()`
(free `/health`, `/ready`, CORS, auth) plus a `cpu_usage` gauge and a `meridian_error_rate` gauge,
an `/admin/fault` + `/admin/clear` pair gated by `common.auth.require_token`, and `/metrics`. Only
the gateway currently has real domain routes wired in (`POST /api/submissions`, `GET /api/reports`)
— the other three exist as genuine, independently-faultable services with the full scaffold, ready
for their own domain endpoints in a later pass. Two tables (`meridian_submissions`,
`meridian_reports`) live on `common.db.METADATA` and are created by Alembic migration
`0004_meridian.py` — no second Postgres, no second Redis; Meridian shares IntelliOps' infra.

The gateway additionally exposes:
- `POST /api/ops/fault` / `POST /api/ops/clear` — a server-side proxy that forwards to the target
  Meridian service's `/admin/fault` / `/admin/clear`, so the browser never holds the admin token.
- `POST /api/ops/deploy` — writes a deploy marker (see §3 below).
- A `StaticFiles` mount at `/` serving the built portal UI (`ui/dist`), registered **last** so it
  never shadows the API/admin/metrics routes above it.

## 2. The portal UI

`services/meridian/ui/` is a second, independent **Vite + React + TypeScript + Tailwind** app,
built by a multi-stage `deploy/Dockerfile.meridian` (`node:20` builds `ui/dist`, then it's copied
into the shared Python image) and served same-origin by the gateway. It is deliberately **not**
styled like the IntelliOps console: the console is dark instrument-panel + cyan + Geist; Meridian
is a light enterprise-fintech look — white / `#F7F8FA` surfaces, near-black ink (`#0B1220`), an
emerald brand accent (`#0E7C5A`) with a navy secondary (`#1B2A4A`), and a **Public Sans / Source
Serif 4 / IBM Plex Mono** type stack (`services/meridian/ui/tailwind.config.js`) — so in the demo
it's visually obvious which screen is "the client's app" and which is "IntelliOps watching it."

Four views:
- **Dashboard** — submissions/reporting-period status, service health at a glance.
- **Submit** — a form that posts a real financial submission through the gateway.
- **Reports** — the reports list.
- **Operations** — the demo driver: the 4 scenario presets, the custom-fault composer, a live
  service-status strip, and the sequential-injection guard (§4).

## 3. How Meridian is wired to IntelliOps (additive only)

IntelliOps' default behavior is **unchanged**. Everything below is additive wiring in the compose
stack and Prometheus config; no IntelliOps service's Python code changed.

**a. Prometheus scrape jobs** (`deploy/prometheus.yml`) — one job per Meridian service, each
stamping a distinct `service` label (the label RCA keys on to attribute an incident):

```yaml
- job_name: meridian-gateway
  static_configs: [{ targets: ["meridian-gateway:8000"], labels: { service: meridian-gateway } }]
- job_name: meridian-validation
  static_configs: [{ targets: ["meridian-validation:8000"], labels: { service: meridian-validation } }]
- job_name: meridian-aggregation
  static_configs: [{ targets: ["meridian-aggregation:8000"], labels: { service: meridian-aggregation } }]
- job_name: meridian-reporting
  static_configs: [{ targets: ["meridian-reporting:8000"], labels: { service: meridian-reporting } }]
```

The pre-existing `demo-app` job is untouched.

**b. The ingestion query, broadened only in compose.** Ingestion polls one fixed PromQL query.
Every Meridian service already emits a `cpu_usage` gauge with the same *name* as `demo-app`'s, so
`scale-service`-flavored faults (a `cpu_usage` spike) are picked up with **zero** query change. To
also see `meridian_error_rate` (needed for the `restart-pod` scenario), the ingestion service's
compose environment sets the query to an instant-vector **selector**:

```yaml
INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'
```

`common/config.py`'s default stays `cpu_usage` — this override lives only in the `ingestion`
service's block in `deploy/docker-compose.yml`, so the default build, the test suite, and CI never
see it. **This was the single riskiest unknown in the design and was verified live**: the regex
selector against a real Prometheus returns `resultType: vector` (an instant vector, exactly what
`PrometheusSource` expects), with each Meridian service appearing as its own series carrying its
`service` label. See §5 for the full verified run.

**c. The `deploys.json` volume (the rollback-deploy path).** Before this work, `rca-service` had
no mount for its on-disk deploy-context file, so `recent_deploys()` was always empty and
`rollback-deploy` could never fire. A new shared named volume, `rca-context`, is mounted at
`/app/data/rca_context` on **both** `rca` and `meridian-gateway`. The gateway's
`POST /api/ops/deploy` writes `deploys.json` = `[{"service": "meridian-gateway", "version":
"v2.3.1", "ts": "..."}]` into that shared volume; `rca`'s enrichment step reads the same file, so a
deploy stamped just before a fault is injected produces a real "recent deployment preceded the
incident" hypothesis (confidence 0.8) — this is a **real, functioning wiring change**, not a
simulated one.

**d. No new playbooks.** `scale-service`, `restart-pod`, and `rollback-deploy` are all
`${service}`-templated in the existing playbook registry, so they already target any service name
Meridian throws at them — no Meridian-specific playbook was needed.

## 4. Fault injection: the mechanism, the scenarios, and why they must run one at a time

Each Meridian service accepts `POST /admin/fault` with `{type, magnitude, duration_seconds?}`
(`services/meridian/common.py`):

| Fault type | Effect | Diagnosis it drives |
|---|---|---|
| `saturation` | `cpu_usage` gauge jumps 18.0 → 92.0 × magnitude | `scale-service` |
| `error` | `meridian_error_rate` rises; `cpu_usage` is deliberately **held at 18.0** | `restart-pod` |
| `latency` | real `time.sleep()` injected on domain routes; also drives `cpu_usage` up | `scale-service` |
| `crash` | `/ready` starts returning 503 | no dedicated RCA rule today — detection-only (see §6) |

The `error` fault's baseline-hold is deliberate and load-bearing: RCA's `rank_hypotheses`
(`services/rca/rank.py`) scores a saturation-token match at confidence 0.6 and an error/log match
at 0.5 — if a fault spiked *both* signals, `scale-service` would always win and `restart-pod` would
never fire. Keeping `cpu_usage` at baseline during an `error` fault is what makes the diagnosis
diverse rather than defaulting to the same playbook every time.

### The 4 scripted scenarios

| Preset | Service | Injected | Expected diagnosis |
|---|---|---|---|
| Aggregation saturated | aggregation | saturation | `scale-service` |
| Report generation slow | reporting | latency (+ cpu) | `scale-service` |
| Validation errors spiking | validation | error (magnitude 0.5) | `restart-pod` |
| Bad gateway deploy | gateway | deploy marker (v2.3.1) then saturation | `rollback-deploy` |

### The custom-fault builder

The Operations view also has a composer: pick a target service, a fault type, a magnitude
(0.1–2.0), a duration, and an optional "mark as deploy" checkbox, then fire it through the same
`/api/ops/fault` proxy the presets use — the identical real mechanism, not a separate code path.
**Honest note on coverage:** only three of the four fault types map to a playbook RCA actually
ranks above the low-confidence fallback (`saturation`/`latency` → `scale-service`,
`error` → `restart-pod`, and a deploy marker → `rollback-deploy`). `crash` (a `/ready` 503) has no
dedicated RCA rule in `rank_hypotheses` — unless it happens to co-occur with a saturation-token
metric, it lands in the generic "root cause undetermined" fallback (confidence 0.2, no suggested
runbook). That is a genuine, current gap in RCA's rule coverage, not a UI bug — the composer will
happily let you inject it, and IntelliOps will detect the anomaly but may not diagnose it richly.

### Why sequential injection is required

`CorrelationEngine` groups anomalies **by time window** (~15 seconds), not by service. Two faults
on two different services fired within the same window merge into a **single** Situation instead
of two distinct ones — the correlator has no per-service isolation. This is a real constraint of
the current detection design, not a Meridian limitation, and it was **confirmed in practice** during
the live verification run (see §5): a stale fault left over from an earlier scenario overlapped a
new one and the situations merged/lingered, exactly as predicted, until faults were cleared and
properly spaced.

The Operations view enforces this: firing a preset or the custom composer is **disabled** while any
fault is active (`guardActive` in `Operations.tsx`), and the UI shows an explicit banner —
*"IntelliOps groups anomalies in a ~15s window — inject one fault at a time. Clear before
starting the next."* — with a **Clear** action. The demo script below follows the same discipline.

## 5. Verified live — the real end-to-end run

This is not a projected or invented result. The full stack (`docker compose up -d --build`, all
services including the four Meridian backends and IntelliOps) was brought up and driven live in
real Docker, sequentially, one scenario at a time.

**The regex ingestion query** — the design's single riskiest unknown — was verified first:
`curl 'http://localhost:9090/api/v1/query' --data-urlencode 'query={__name__=~"cpu_usage|meridian_error_rate"}'`
returned `resultType: vector` (a correct instant vector). All 5 Prometheus targets were `up`
(`demo-app` + the four `meridian-*` jobs), and each Meridian service appeared as its own series
carrying its `service` label, with both `cpu_usage` (18, baseline) and `meridian_error_rate` (0)
present.

Three scenarios were then run **sequentially**, each proving a genuinely different diagnosis:

| Scenario | Service | Injected | cpu_usage | error_rate | Diagnosis (runbook) | Result |
|---|---|---|---|---|---|---|
| Aggregation saturated | meridian-aggregation | saturation | 18 → 92 | 0 | resource saturation (0.6) → **scale-service** | matched expectation |
| Validation errors | meridian-validation | error, magnitude 0.6 | stays 18 | 0.6 | error spike (0.5) → **restart-pod** | matched expectation |
| Bad gateway deploy | meridian-gateway | deploy marker v2.3.1 + saturation | 18 → 92 | 0 | recent-deploy (0.8) outranks saturation (0.6) → **rollback-deploy** | matched expectation |

Each pipeline hop was real: Meridian fault → Prometheus scrape (with the `service` label) →
ingestion (the regex query) → `telemetry.raw` → correlation z-score detection →
`situations.detected` → RCA ranking → the expected playbook, reaching `status=diagnosed` and then
the action/governance path.

**The validation-errors invariant held live:** the error fault kept `cpu_usage` at 18 while
`error_rate` rose to 0.6, so `restart-pod` (0.5) fired instead of `scale-service` (0.6) — exactly
the design in §4, confirmed against real Prometheus values, not just unit tests.

**The rollback-deploy story held live end to end:** `/api/ops/deploy` wrote
`{"service":"meridian-gateway","version":"v2.3.1","ts":...}` into the shared `rca-context` volume;
RCA read it and produced *"recent deployment of meridian-gateway (v2.3.1) preceded the incident"*
at confidence 0.8, outranking the concurrent 0.6 saturation hypothesis — proving the volume-sharing
wiring in §3c actually works, not just that the code compiles.

**The HITL gate fired correctly.** All four outcomes reached `action-service` and came back
`result=failure, reason=aborted:timeout` — this is **correct**, not a bug: these are `hitl_mode`
playbooks, and the live-verification run did not approve them (by design, to prove the gate holds).
In an actual demo, the operator approves the pending request in the IntelliOps console, and with
`REMEDIATOR_MODE=dry_run` (the default) the remediation completes — the action service logs the
steps and the health check reports healthy.

**The time-window-merge behavior was directly observed, not just theorized.** During the live run,
a stale saturation fault (left at cpu=92 from an earlier step, cleared via the wrong endpoint)
overlapped a newly-injected fault within the ~15s correlation window, and the resulting situations
merged/lingered instead of appearing as two distinct incidents. Fixing it required the exact
discipline described in §4 — clear via `/api/ops/clear`, reset the correlator baseline, and wait out
a full window before the next injection. This is why the sequential-injection guard exists and why
the demo script below insists on it.

## 6. The demo script (the money shot)

1. **Bring the stack up.** `docker compose -f deploy/docker-compose.yml up -d --build`. Wait for
   all services — the six IntelliOps services, read-service, the four `meridian-*` backends, and
   `meridian-gateway` — to report healthy.
2. **Open the Meridian portal** at `http://localhost:8008`. Everything green — *"the enterprise
   platform we operate for the client."* Submit a financial record or two so real traffic flows.
3. **Go to the Operations view** and fire **one** preset (or a custom fault). The service-status
   strip degrades for that one service.
4. **Switch to the IntelliOps console** and watch the incident move through the pipeline: detected
   → diagnosed (with the expected playbook attached) → the HITL gate.
5. **Approve** the pending request in the console. With the default `dry_run` remediator, the
   action service logs the remediation steps and the health check reports healthy; the outcome
   resolves.
6. **Clear the fault** from the Meridian Operations view (respecting the sequential-injection guard
   — wait for the "no active fault" state) before firing the next scenario.
7. Repeat for the remaining scenarios to show the **diverse** diagnoses — `scale-service`,
   `restart-pod`, `rollback-deploy` — genuinely earned from different fault signatures, not a
   hardcoded demo path.

## 7. Honest limits

- **Synthetic data.** Submissions, reports, and financial figures are placeholder values — Meridian
  is a realistic *shape*, not real client data or real audit logic.
- **Toggle-based faults, not organic failures.** Every fault is a deliberate state flip
  (`MeridianState.apply`) triggered by an admin call, not an emergent failure mode. This mirrors
  `services/demo_app`'s existing `/break`/`/fix` pattern and is what makes the demo repeatable, but
  it means Meridian never fails in a way its own code didn't explicitly script.
- **Faults must be injected one at a time.** Correlation groups by time window, not by service
  (§4) — this is a real constraint of the current detector, confirmed live (§5), not just a UI
  restriction. Concurrent faults on different services will merge into one Situation.
- **Not every fault type has a dedicated playbook.** `crash` has no RCA rule of its own today (§4)
  — it is detection-capable but not richly diagnosable, and the custom-fault composer does not
  currently flag this distinction in its own UI text (it is documented here instead).
- **Only the gateway has real domain routes today.** `validation`, `aggregation`, and `reporting`
  are fully faultable, independently-observed services with the complete scaffold, but their
  `/validate`, `/aggregate`, and `/report` domain endpoints are not yet wired to real business
  logic — they are genuine fault targets, not yet full request handlers.
- **The demo uses dry-run remediation by default.** `REMEDIATOR_MODE=dry_run` (the IntelliOps-wide
  default) means an approved fix logs its steps and a simulated health check reports success — no
  container is actually restarted. Real remediation against Meridian would require pointing
  `REMEDIATOR_MODE=k8s` at a cluster running Meridian's workloads, which is out of scope for the
  compose-based demo described here (see `deploy/k8s/README.md` for the existing k8s remediation
  path against the original demo-app).
- **IntelliOps' own default behavior is unchanged.** Everything in §3 is additive — a fresh
  `docker compose up` without the Meridian services, and the full `pytest` suite, both see the
  original `cpu_usage`-only ingestion query and no Meridian-specific code path.
