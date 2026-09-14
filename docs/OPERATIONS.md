# Operations

Stream D (platform, security, CI/CD) owns this doc. Sections beyond auth
(Kafka binding, K8s deploy, load/chaos numbers) land as those pieces ship.

## Auth at the edge

Controlled by `INTELLIOPS_AUTH_MODE`:

| Value | Behavior |
| --- | --- |
| `off` (default) | Every endpoint open. Current dev/test/CI behavior, unchanged. |
| `token` | Every request except `/health` and `/ready` must carry `Authorization: Bearer <INTELLIOPS_AUTH_TOKEN>`, or the service returns `401`. |

Set `INTELLIOPS_AUTH_TOKEN` to the shared token when `AUTH_MODE=token`. A
service started in `token` mode with no `AUTH_TOKEN` set rejects every
protected request — there's no accidental-open fallback.

`/health` (liveness) and `/ready` (readiness) are exempt in every mode, on
every service, so docker-compose healthchecks, k8s liveness/readiness probes,
and CI's compose-smoke job never need a token.

### What's gated

Applied via the shared app factory (`services/base.py`), so it covers every
route on ingestion, correlation, rca, action, feedback, governance, and
read — except `/health` and `/ready`, which are always exempt (probes never
need a token).

In `token` mode **all** endpoints are gated — including the internal
service-to-service paths on governance (`POST /audit`, `POST /rbac/check`,
`POST /approvals`, `GET /approvals/{id}`, `POST /playbooks/{id}/graduate`).
Internal callers (action's `HttpGovernanceGate`, feedback's graduator)
attach the shared `Bearer` token to their requests automatically.

demo-app doesn't use the shared factory (it's an external target, not an
IntelliOps service), so it's gated per-route instead: `/break` and `/fix`
(simulation controls) require the token in `token` mode; `/health`,
`/metrics` (scraped by Prometheus, unauthenticated), and `/work`
(simulated app traffic) stay open.

### The React console

The operator console authenticates the same way. Under `AUTH_MODE=token`,
set `VITE_AUTH_TOKEN` to the same value as `INTELLIOPS_AUTH_TOKEN`; the
frontend attaches `Authorization: Bearer <VITE_AUTH_TOKEN>` on every call —
the read/governance-read fetches (`/situations`, `/outcomes`, `/audit`,
`/playbooks`, `/metrics`) as well as the approve/reject write. Because those
reads are gated, `token` mode leaves **no public read surface**.

Vite inlines `VITE_*` vars at build time, so the console token is baked into
the client bundle. That makes it a **shared demo token, not a per-user
secret** — anyone who can load the bundle has it. A real deployment would
issue per-user tokens or front the console with an IdP; the shared token is a
demo convenience, not the production auth model.

### Compose: shared secret for token mode

When running in `AUTH_MODE=token`, every service that makes or receives
authenticated HTTP calls must share the same `INTELLIOPS_AUTH_TOKEN`.
In `deploy/docker-compose.yml`, add the following env vars to the services
that talk to governance over REST:

| Service | Why it needs the token |
| --- | --- |
| `governance` | Validates incoming tokens on all endpoints. |
| `action` | `HttpGovernanceGate` calls `POST /rbac/check`, `POST /audit`, `POST /approvals`, `GET /approvals/{id}`. |
| `feedback` | `_make_graduator` calls `POST /playbooks/{id}/graduate`. |
| `rca` | Uses the shared factory; gated if exposed. |

Example compose environment block (add to each service above):

```yaml
environment:
  INTELLIOPS_AUTH_MODE: token
  INTELLIOPS_AUTH_TOKEN: ${INTELLIOPS_AUTH_TOKEN:?Set a shared secret}
```

Then launch with:

```bash
INTELLIOPS_AUTH_TOKEN=my-secret docker compose -f deploy/docker-compose.yml up -d
```

### Not yet covered

RBAC inside governance-service (who can approve what) is unrelated to this
and already existed — this only gates network access to the HTTP surface.

---

## Delivery guarantees

Delivery semantics are selected by **`INTELLIOPS_BUS_DELIVERY`**. The default is
unchanged; everything below is opt-in.

### `at_most_once` (default)

`RedisBus.consume` XACKs an entry **before** yielding it, and `KafkaBus` runs with
`enable_auto_commit=True`. A process crash, validation error or DB failure between
receiving a message and finishing it **loses that message** - it is not redelivered.

### `at_least_once`

The ack fires the instant the caller **resumes** the generator: being resumed is the only
proof the handler finished. (It is not deferred until the next entry happens to arrive -
that would leave a completed entry pending indefinitely on an idle topic, and a restart
would then re-serve work that was already done.) If the handler raises, breaks on `stop_event`, or
abandons the generator, the entry stays pending.

Deferring the ack alone would make events durable but *unreachable*, because
`XREADGROUP ">"` never returns pending entries. So `consume()` first drains this
consumer's **own** pending entries (start id `"0"`), then tails. That makes the consumer
name load-bearing: it **must stay stable across restarts** (`INTELLIOPS_BUS_CONSUMER_NAME`,
default `c1`). Scaling a consumer past `replicas: 1` additionally needs per-pod names plus
`XAUTOCLAIM` - see [ADR-033](../architectural.md).

Redelivery is only safe if consumers can recognise a repeat, so turn on
`INTELLIOPS_BUS_IDEMPOTENCY_MODE=redis` alongside it. The action service goes further: it
takes a two-phase claim per situation, and if a previous attempt died mid-flight it emits
an explicit `interrupted:unknown` outcome for a human rather than re-running a real
remediation on a guess.

### Measured

`scripts/delivery_probe.py` publishes N events, crashes the handler partway, restarts the
consumer under the same name, and counts what was actually handled. Against the compose
stack:

```
$ python scripts/delivery_probe.py --count 50 --crash-at 25
delivery mode      at_most_once
published          50
completed (total)  49
LOST               1          <- acked but never handled, never redelivered

$ INTELLIOPS_BUS_DELIVERY=at_least_once python scripts/delivery_probe.py --count 50 --crash-at 25
delivery mode      at_least_once
published          50
completed (total)  50
LOST               0          <- the in-flight event came back
```

### Poison messages

Under at-least-once, a payload this build cannot decode would be redelivered forever.
`INTELLIOPS_BUS_DLQ_MODE=on` parks it on `{topic}.dlq` after
`INTELLIOPS_BUS_MAX_DELIVERY_ATTEMPTS` (default 5) with a `_dlq_reason`, and the consumer
moves on. The DLQ is enforced in `iter_models`, not in the bus: a generator cannot observe
the exception its consumer raised (it receives `GeneratorExit`), so the frame that calls
`decode_model` is the only place a poison payload is visible.

### Deploy ordering when a contract enum gains a member

Required **only in the default `at_most_once` mode**, where an undecodable payload is lost
rather than retried. `decode_model` uses `model_validate_json`, which raises
`ValidationError` on an unknown enum value; that exception escapes the
`for ... in iter_models(...)` statement itself, so `try/except` blocks *inside* consumer
loop bodies do not cover it. Each outcomes consumer runs on a bare daemon thread with no
supervisor, so the thread dies silently and never restarts - and the message was already
acked. The visible symptom is not an error: the dashboard simply stops updating.

**Therefore: deploy consumers before producers.** For the `RemediationResult.ESCALATED`
addition that meant read-service, feedback-service and governance-service **first**, and
action-service **last** - or all of them in one rollout.

Turning on `at_least_once` **plus** `bus_dlq_mode=on` removes this hazard: the undecodable
message is retried, then parked in the DLQ for inspection, and the consumer survives.

---

## Chaos: killing a service mid-stream

The recovery story, measured rather than asserted. `scripts/chaos.sh` drives one incident;
this is the resilience check.

**Scenario.** SIGKILL the correlation service while it is actively consuming
`telemetry.raw`, restart it, and watch the consumer group.

```
== before ==
   telemetry.raw length   : 60561
   correlation group      : consumers 1  pending 0  entries-read 60623  lag 0

== killing correlation (SIGKILL, mid-stream) ==
   healthy again after 9s

== after ==
   telemetry.raw length   : 60809 (was 60561)
   correlation group      : consumers 1  pending 0  entries-read 60871  lag 0
   consumer still registered: name c1  pending 0
```

**What this shows.** The consumer group survives the process: `c1` is still registered
after the kill, resumes from its own offset, and returns to `lag 0` - it caught up on the
248 entries published while it was down rather than skipping them. Recovery to a healthy
readiness probe took **9 seconds**, unattended.

**What it does not show.** `pending 0` here is the *default* at-most-once mode doing what
it always does: entries are acked on read, so nothing is ever pending and nothing is ever
redelivered. Any message that was mid-handler at the instant of the kill is gone, and this
view cannot distinguish that from clean progress. That is precisely what the
`delivery_probe` numbers above measure, and why `at_least_once` exists.


---

## Kubernetes deploy

### Prerequisites

- A running [kind](https://kind.sigs.k8s.io/) cluster.
- The `intelliops` Docker image built and loaded into the cluster:
  ```bash
  docker build -t intelliops .
  kind load docker-image intelliops
  ```

### Install command

```bash
helm install intelliops deploy/k8s/platform/
```

This deploys all platform services, Redis, Postgres, and runs the Alembic migration
job (`alembic upgrade head`) as a pre-install hook before any application pod starts.

To upgrade an existing release:

```bash
helm upgrade intelliops deploy/k8s/platform/
```

### Full stack, live posture

`helm install` alone brings up the platform in the **safe** posture (dry-run,
selector off, LLM template, health `always`, no RBAC) — nothing in the cluster is
remediated for real. To run *everything* in-cluster with the metrics-arc AI
features live, use the one-command bring-up and the live overlay
([ADR-030](../architectural.md#adr-030--full-in-cluster-deployment-helm),
[deploy/k8s/README.md](../deploy/k8s/README.md)):

```bash
GROQ_API_KEY=gsk_... ./scripts/kind-up-full.sh
```

The live overlay (`deploy/k8s/platform/values-live.yaml`) flips these — **no new
config keys**, it reuses the same `INTELLIOPS_*` settings the services already
read:

| Setting | Safe default | Live |
|---|---|---|
| `INTELLIOPS_CORRELATOR_KIND` | `river` | `robust` |
| `INTELLIOPS_DETECTION_POLICY` | `off` | `on` |
| `INTELLIOPS_RUNBOOK_SELECTOR_MODE` | `off` | `embedding` |
| `INTELLIOPS_REMEDIATOR_MODE` | `dry_run` | `k8s` |
| `INTELLIOPS_SANDBOX_MODE` | `off` | `k8s` |
| `INTELLIOPS_HEALTH_CHECK_MODE` | `always` | `k8s` |
| `INTELLIOPS_LLM_EXPLANATION_ENDPOINT`/`_MODEL` | empty | set |
| `rbac.create` (chart) | `false` | `true` |

`rca` and `action` run the `full` image (ml + k8s extras, CPU torch + baked
embedding model); the other five use the lean `base` image. The **LLM API key is
never committed** — supply it at install via `--set-string llm.apiKey=…` (the
chart creates a Secret) or pre-create a Secret and set `llm.apiKeySecretName`; it
reaches only the `rca` pod via `secretKeyRef`, never the ConfigMap.

---

## Environment-switch reference

The table below covers every runtime toggle. For full `AUTH_MODE` / `AUTH_TOKEN`
usage (service-to-service token propagation, compose setup, what endpoints are
gated) see the [Auth at the edge](#auth-at-the-edge) section above.

| Variable | Accepted values | Default | Description |
| --- | --- | --- | --- |
| `INTELLIOPS_AUTH_MODE` | `off`, `token` | `off` | Network-access gate. `off` = open. `token` = every non-`/health` endpoint requires `Authorization: Bearer <INTELLIOPS_AUTH_TOKEN>`. See [Auth at the edge](#auth-at-the-edge). |
| `INTELLIOPS_STORE_BACKEND` | `file`, `postgres` | `file` | Persistence layer. `file` = JSONL files on disk (test-safe, no DB needed). `postgres` = PostgreSQL via SQLAlchemy (requires `INTELLIOPS_DATABASE_URL`). |
| `INTELLIOPS_BUS_BACKEND` | `redis`, `kafka` | `redis` | Event-bus binding. `redis` = Redis Streams (`RedisBus`). `kafka` = Kafka (`KafkaBus`, requires `INTELLIOPS_KAFKA_BOOTSTRAP_SERVERS`). Both bindings use at-most-once delivery — see [Delivery guarantees](#delivery-guarantees). |
| `INTELLIOPS_REMEDIATOR_MODE` | `dry_run`, `k8s` | `dry_run` | Remediation execution mode. `dry_run` = log steps only, no real infrastructure changes (CI/test default). `k8s` = execute playbook steps against a real Kubernetes cluster via the official `kubernetes` Python client (requires a valid kubeconfig and `INTELLIOPS_K8S_NAMESPACE`). |
| `INTELLIOPS_CORRELATOR_KIND` | `river`, `robust`, `trained` | `river` | Correlator implementation (`services/correlation`). `river` = online z-score, unchanged default. `robust` = median/MAD + per-hour seasonal baseline (fixes river's seasonal false-positive and single-spike-desensitizes weaknesses). `trained` = `robust`'s online score blended with a persisted scikit-learn `IsolationForest` (fit via `POST /retrain`, not automatic). See [docs/BENCHMARKS.md](BENCHMARKS.md) and [ADR-019](../architectural.md#adr-019--pluggable-detectors-the-finetuning-loop-and-llm-assisted-rca). |
| `INTELLIOPS_CORRELATION_GROUP_BY` | `window`, `service` | `window` | How anomalies are bucketed before windowing (`services/correlation`). `window` (default, unchanged) puts every anomaly in one bucket, so two services failing inside the same ~30s window collapse into a **single** Situation — this is why the Meridian ops panel serialises fault injection. `service` buckets by the event's `service` label, so concurrent faults on different services stay separate incidents, each attributed and diagnosed on its own; unlabelled events share one bucket. Buckets are independent: one service's window overflowing does not flush another's. |
| `INTELLIOPS_BUS_DELIVERY` | `at_most_once`, `at_least_once` | `at_most_once` | Bus delivery semantics (`common/bus.py`). Default acks each entry BEFORE the handler runs, so a crash mid-handler loses it. `at_least_once` defers the ack until the caller returns for the next entry and re-drains this consumer's own pending entries on reconnect. Measured loss on a crash: 1 vs 0 (see Delivery guarantees). Pair it with `INTELLIOPS_BUS_IDEMPOTENCY_MODE`. |
| `INTELLIOPS_BUS_CONSUMER_NAME` | any string | `""` (→ `c1`) | Redis consumer name. **Must stay stable across restarts** under `at_least_once`: the pending-entry self-drain re-serves entries recorded against this name, so a per-process name would strand them. |
| `INTELLIOPS_BUS_DLQ_MODE` | `off`, `on` | `off` | Park undecodable or repeatedly-redelivered messages on `{topic}.dlq` instead of letting them wedge a consumer. Independent of `bus_delivery`: DLQ-on with `at_most_once` already stops a decode error killing a consumer thread. |
| `INTELLIOPS_BUS_MAX_DELIVERY_ATTEMPTS` | int | `5` | Deliveries before an entry is parked in the DLQ. Consulted only when the DLQ is on. |
| `INTELLIOPS_BUS_IDEMPOTENCY_MODE` | `off`, `redis`, `memory` | `off` | How consumers recognise a redelivered event (`common/idempotency.py`). `off` = every guard call site is inert (today). `redis` shares the bus client and survives a restart. `memory` is process-local and does NOT survive a restart — the fallback when there is no Redis client (e.g. `BUS_BACKEND=kafka`). |
| `INTELLIOPS_BUS_IDEMPOTENCY_TTL_SECONDS` | int | `86400` | TTL on idempotency keys; also self-prunes the delivery-attempt counters. |
| `INTELLIOPS_READ_REBUILD_MODE` | `off`, `replay` | `off` | Cold-start rebuild of the read projection (`services/read/rebuild.py`). `off` = a restarted read-service resumes past its acks and shows an empty console until new traffic arrives. `replay` re-reads a bounded window of the raw streams into a shadow model first, and only swaps it in if every topic replayed cleanly. |
| `INTELLIOPS_READ_REBUILD_WINDOW_SECONDS` | float | `3600` | How far back the replay reaches. |
| `INTELLIOPS_READ_REBUILD_MAX_ENTRIES` | int | `20000` | Safety valve. Tripping it discards the WHOLE rebuild and cold-starts, rather than serving a partial projection. |
| `INTELLIOPS_CORRELATION_SEASONAL_BUCKETS` | integer | `24` | Number of hour-of-day buckets `robust`/`trained` keep independent baselines for. |
| `INTELLIOPS_CORRELATION_ROBUST_WINDOW` | integer | `128` | Max samples kept per `(metric, hour-bucket)` window for `robust`/`trained`'s median/MAD calculation. |
| `INTELLIOPS_CORRELATION_ROBUST_WARMUP` | integer | `30` | Samples required in a bucket before `robust`/`trained` scores it (below this, score is `0`, like `river`'s warm-up gate). |
| `INTELLIOPS_DETECTION_POLICY` | `off`, `on` | `off` | Metric-kind-aware anomaly decision layered over the per-metric z-score (`services/correlation`). `off` = pure z-score (`score > z_threshold`) for every metric, byte-identical to pre-policy behavior (CI/test default). `on` = classify each metric by name (`ratio`/`saturation`/`latency`/`default`) and apply the matching rule — absolute thresholds for `ratio`/`saturation`, the statistical score **or** an absolute ceiling for `latency`. Note: the `latency` kind's statistical half is only genuinely seasonal when `INTELLIOPS_CORRELATOR_KIND=robust` or `trained`; under the `river` default the ceiling is doing the seasonal-false-positive-guarding work. See [ADR-027](../architectural.md#adr-027--detection-policy-per-metric-kind). |
| `INTELLIOPS_DETECTION_RATIO_THRESHOLD` | float | `0.02` | Absolute cutoff for `ratio`-kind metrics (name contains `error_rate` / `error_ratio` / `_ratio`) when `DETECTION_POLICY=on`. A metric's raw value above this fires, regardless of z-score. |
| `INTELLIOPS_DETECTION_SATURATION_RATIO_THRESHOLD` | float | `0.80` | Absolute cutoff for 0..1-scaled `saturation`-kind metrics (e.g. `saturation`, `utilization`, `disk_usage`) when `DETECTION_POLICY=on`. |
| `INTELLIOPS_DETECTION_SATURATION_PERCENT_THRESHOLD` | float | `90.0` | Absolute cutoff for 0..100/percent-scaled `saturation`-kind metrics (`cpu_usage`, any `_percent` name) when `DETECTION_POLICY=on`. |
| `INTELLIOPS_DETECTION_LATENCY_CEILING_MS` | float | `500.0` | Absolute latency ceiling (ms) for `latency`-kind metrics (`latency` / `duration` / `_ms`) when `DETECTION_POLICY=on` — fires alongside the statistical score, whichever trips first. This is the fallback that matters most under `river` (no seasonal baseline); `robust`/`trained` catch seasonal latency via their own per-hour baseline. |
| `INTELLIOPS_HEALTH_CHECK_MODE` | `always`, `k8s` | `always` | Post-remediation health verification (`services/action`). `always` = `AlwaysHealthyChecker`, no real check (CI/test default; dry-run unaffected by anything below). `k8s` = verify against a real cluster: pod-readiness AND per-metric recovery — the metric(s) that actually fired are re-checked against the **same** `DETECTION_*` + `INTELLIOPS_CORRELATION_Z_THRESHOLD` policy/threshold correlation detects with (no separate config; detect and verify agree by construction), replacing the old hardcoded `cpu_usage < 50` check. A firing metric with no usable baseline, or a failed query, fails safe to not-recovered → rollback. The sandbox pre-flight rehearsal (`INTELLIOPS_SANDBOX_MODE=k8s`) reuses the identical per-metric check on its post-fix step. See [ADR-029](../architectural.md#adr-029--per-metric-health-verification). |
| `INTELLIOPS_LLM_EXPLANATION_ENDPOINT` | URL or empty | `""` (empty) | RCA explanation provider selector (`services/rca`). Empty = `TemplateExplanationProvider` (deterministic, no network — CI/test default). Set to an OpenAI-compatible base URL (OpenAI, local Ollama, vLLM, …) to use `OpenAICompatibleExplanationProvider`; any call failure (timeout, non-200, bad body) falls back to the template. The explanation is advisory-only — it never affects hypothesis confidence, ordering, or the suggested runbook. |
| `INTELLIOPS_LLM_EXPLANATION_MODEL` | string | `gpt-4o-mini` | Model name sent in the chat-completions request when an LLM endpoint is configured. |
| `INTELLIOPS_LLM_EXPLANATION_TIMEOUT_SECONDS` | float | `10.0` | Request timeout for the LLM explanation call before falling back to the template. |
| `INTELLIOPS_LLM_EXPLANATION_API_KEY` | string | `""` (empty) | Bearer token sent as `Authorization: Bearer <key>` to the LLM endpoint, if set. |
| `INTELLIOPS_SANDBOX_MODE` | `off`, `k8s` | `off` | Pre-flight rehearsal (`services/action`). `off` = `NullSandbox`, no rehearsal (CI/test default, base path byte-identical). `k8s` = `NamespaceCloneSandbox` clones the target Deployment into a throwaway namespace and rehearses the fix **before** approval; a failed rehearsal blocks an `auto` playbook and advises a `hitl` human. Requires the same kubeconfig as `REMEDIATOR_MODE=k8s`. See [ADR-023](../architectural.md#adr-023--pre-flight-sandbox-rehearsal-before-remediation). |
| `INTELLIOPS_RUNBOOK_AUTHOR_MODE` | `off`, `openai` | `off` | AI runbook drafting (`services/governance`). `off` = `NullRunbookAuthor`, drafting disabled (CI/test default). `openai` = `OpenAICompatibleRunbookAuthor` drafts a typed `Playbook` for a gap on human request (requires `INTELLIOPS_LLM_RUNBOOK_ENDPOINT`); the draft is stored as a proposal a human must approve before it joins the registry — the type system rejects unsafe drafts. See [ADR-025](../architectural.md#adr-025--ai-authored-runbooks-propose--approve). |
| `INTELLIOPS_LLM_RUNBOOK_ENDPOINT` | URL or empty | `""` (empty) | OpenAI-compatible base URL for the runbook author (OpenAI, local Ollama, vLLM, …). Empty ⇒ author stays `Null` even if `RUNBOOK_AUTHOR_MODE=openai`. |
| `INTELLIOPS_LLM_RUNBOOK_MODEL` | string | `gpt-4o-mini` | Model name sent in the chat-completions request when the runbook author endpoint is configured. |
| `INTELLIOPS_LLM_RUNBOOK_TIMEOUT_SECONDS` | float | `10.0` | Request timeout for the runbook-author call before it gives up (returns no draft). |
| `INTELLIOPS_LLM_RUNBOOK_API_KEY` | string | `""` (empty) | Bearer token sent to the runbook-author endpoint, if set. |
| `INTELLIOPS_SYSTEM_CONTEXT_PATH` | file path | `config/system_context.yaml` | Path to the curated, system-agnostic description of the target system the runbook author reads for grounding (`services/governance`). Baked into both Docker image stages at this path (`deploy/Dockerfile`), so no volume or extra config is needed to have it present; override only to point at a different mounted/baked location. See [System context for the runbook author](#system-context-for-the-runbook-author) below. |
| `INTELLIOPS_RUNBOOK_SELECTOR_MODE` | `off`, `embedding` | `off` | Semantic runbook selection (`services/rca`). `off` = `NullRunbookSelector`, keyword-rules-only (CI/test default, selection byte-identical to before). `embedding` = when no keyword rule fires, `EmbeddingRunbookSelector` ranks the **registered** playbooks by embedding similarity of their `symptoms` field and picks the best above the threshold (requires the `ml` extra; retrieval among vetted playbooks, never an LLM choosing). See [ADR-026](../architectural.md#adr-026--semantic-runbook-selection-embedding-fallback). |
| `INTELLIOPS_RUNBOOK_SELECTOR_MODEL` | string | `all-MiniLM-L6-v2` | `sentence-transformers` model used by the embedding selector (loaded lazily, offline, no API). |
| `INTELLIOPS_RUNBOOK_SELECTOR_THRESHOLD` | float | `0.45` | Minimum cosine similarity for the embedding selector to accept a match; below it, the incident falls to the gap (where the AI-authoring flow can draft one). |

### System context for the runbook author

The AI runbook author (`INTELLIOPS_RUNBOOK_AUTHOR_MODE=openai`, itself off by
default — see the table above) can optionally be grounded in a description of
the real system it is drafting for, so its drafts reference actual services,
dependencies, and known remediation quirks instead of generic advice.

That description lives in `config/system_context.yaml`, read by governance's
`SystemContextProvider` at the path in `INTELLIOPS_SYSTEM_CONTEXT_PATH`. The
file shipped in the image is an **empty placeholder** — every field blank —
which `SystemContextProvider` treats as **"unconfigured"**: the author still
drafts normally, from the incident and its retrieved history of past
decisions/outcomes alone. A missing or malformed file is handled the same
way (logged, never raised). Filling the file in is purely additive context;
it does not change the author's tool-calling/retrieval behavior, gate it
behind a model, or bypass any existing safety step — a drafted playbook still
goes through the same type-checked validation, sandbox, and human-approval
gate as any other proposal.

Schema (see the checked-in comments in `config/system_context.yaml` for the
authoritative reference):

```yaml
system:
  name: ""      # e.g. "Payments API"
  summary: ""   # one line: what the system does

services: []    # each entry:
  # - name: "auth-svc"
  #   role: "authorizes card transactions"
  #   depends_on: ["db", "cache"]
  #   key_metrics: ["error_rate", "latency_p99"]

# actions:                       # optional free-form remediation hints
#   restart_policy: "kill -9 then systemctl restart"
#   rollback_window: "5 minutes max"

# notes: ""                      # optional additional free-text context
```

To point at a different file (e.g. mounted from a ConfigMap or Secret volume
instead of the baked default), set `INTELLIOPS_SYSTEM_CONTEXT_PATH` — via
`deploy/k8s/platform/values.yaml`'s `env.SYSTEM_CONTEXT_PATH` in a Helm
deploy, or the environment variable directly in compose/local dev.

---

## Agent Activity

The "Agent Activity" console tab displays the AI runbook author's step-by-step reasoning and tool calls when drafting a runbook. The trace shows:

- The reasoning context and available tools
- Each tool call the agent makes (runbook lookup, similarity ranking, etc.)
- The result of each tool call
- The final draft proposal

The trace **streams live** to the console over Server-Sent Events (`GET /api/gov/agent-runs/{run_id}/stream`) and is persisted in Postgres (`agent_runs` and `agent_run_steps` tables) for audit and replay. The trace is **best-effort only** — it is an observer of the agent's decisions and never changes what the agent drafts or gates; it only populates when the AI author actually runs (author mode on, a "Draft a runbook with AI" click is made).

The nginx reverse proxy disables buffering on the `/api/gov/` path to ensure SSE events reach the console immediately — see `deploy/nginx.conf` and the `proxy_buffering off` directive.
