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

Both `RedisBus` (Redis Streams) and `KafkaBus` (Kafka with `enable_auto_commit=True`)
use **at-most-once** delivery semantics. The offset/acknowledgment is advanced as
soon as the message is read, before the caller finishes processing it.

Consequence: a process crash between reading a message and completing its processing
loses the in-flight message on both backends — it will not be redelivered.

Upgrading to **at-least-once** delivery requires changing the ack/commit point on
**both** bindings together:
- `RedisBus`: move `XACK` to after the caller has processed the message.
- `KafkaBus`: switch to `enable_auto_commit=False` and call `consumer.commit()` after
  processing.

This is a deliberate future decision; the current at-most-once semantics match the
project's scale requirements.

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
| `INTELLIOPS_CORRELATION_SEASONAL_BUCKETS` | integer | `24` | Number of hour-of-day buckets `robust`/`trained` keep independent baselines for. |
| `INTELLIOPS_CORRELATION_ROBUST_WINDOW` | integer | `128` | Max samples kept per `(metric, hour-bucket)` window for `robust`/`trained`'s median/MAD calculation. |
| `INTELLIOPS_CORRELATION_ROBUST_WARMUP` | integer | `30` | Samples required in a bucket before `robust`/`trained` scores it (below this, score is `0`, like `river`'s warm-up gate). |
| `INTELLIOPS_LLM_EXPLANATION_ENDPOINT` | URL or empty | `""` (empty) | RCA explanation provider selector (`services/rca`). Empty = `TemplateExplanationProvider` (deterministic, no network — CI/test default). Set to an OpenAI-compatible base URL (OpenAI, local Ollama, vLLM, …) to use `OpenAICompatibleExplanationProvider`; any call failure (timeout, non-200, bad body) falls back to the template. The explanation is advisory-only — it never affects hypothesis confidence, ordering, or the suggested runbook. |
| `INTELLIOPS_LLM_EXPLANATION_MODEL` | string | `gpt-4o-mini` | Model name sent in the chat-completions request when an LLM endpoint is configured. |
| `INTELLIOPS_LLM_EXPLANATION_TIMEOUT_SECONDS` | float | `10.0` | Request timeout for the LLM explanation call before falling back to the template. |
| `INTELLIOPS_LLM_EXPLANATION_API_KEY` | string | `""` (empty) | Bearer token sent as `Authorization: Bearer <key>` to the LLM endpoint, if set. |
