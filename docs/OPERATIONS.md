# Operations

Stream D (platform, security, CI/CD) owns this doc. Sections beyond auth
(Kafka binding, K8s deploy, load/chaos numbers) land as those pieces ship.

## Auth at the edge

Controlled by `INTELLIOPS_AUTH_MODE`:

| Value | Behavior |
| --- | --- |
| `off` (default) | Every endpoint open. Current dev/test/CI behavior, unchanged. |
| `token` | Every request except `/health` must carry `Authorization: Bearer <INTELLIOPS_AUTH_TOKEN>`, or the service returns `401`. |

Set `INTELLIOPS_AUTH_TOKEN` to the shared token when `AUTH_MODE=token`. A
service started in `token` mode with no `AUTH_TOKEN` set rejects every
protected request — there's no accidental-open fallback.

`/health` is exempt in every mode, on every service, so docker-compose
healthchecks, k8s liveness/readiness probes, and CI's compose-smoke job
never need a token.

### What's gated

Applied via the shared app factory (`services/base.py`), so it covers every
route on ingestion, correlation, rca, action, feedback, governance, and
read — except `/health`.

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
