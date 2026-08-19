# Operations

Stream D (platform, security, CI/CD) owns this doc. Sections beyond auth
(Kafka binding, K8s deploy, load/chaos numbers) land as those pieces ship.

## Auth at the edge

Controlled by `INTELLIOPS_AUTH_MODE`:

| Value | Behavior |
| --- | --- |
| `off` (default) | Every endpoint open. Current dev/test/CI behavior, unchanged. |
| `token` | Every request except `/health` (and demo-app's `/metrics`, `/work`) must carry `Authorization: Bearer <INTELLIOPS_AUTH_TOKEN>`, or the service returns `401`. |

Set `INTELLIOPS_AUTH_TOKEN` to the shared token when `AUTH_MODE=token`. A
service started in `token` mode with no `AUTH_TOKEN` set rejects every
protected request — there's no accidental-open fallback.

`/health` is exempt in every mode, on every service, so docker-compose
healthchecks, k8s liveness/readiness probes, and CI's compose-smoke job
never need a token.

### What's gated

Applied via the shared app factory (`services/base.py`), so it covers every
route on ingestion, correlation, rca, action, governance, feedback, and
read — except `/health`. In practice the endpoints this actually protects
are the ones with real external read/write surface:

- **read-service** — `/situations`, `/outcomes`, `/metrics`, `/reset`
- **governance-service** — `/audit`, `/playbooks`, `/rbac/check`, `/approvals`
- **correlation-service** — `/reset-baseline` (simulation control)

demo-app doesn't use the shared factory (it's an external target, not an
IntelliOps service), so it's gated per-route instead: `/break` and `/fix`
(simulation controls) require the token in `token` mode; `/health`,
`/metrics` (scraped by Prometheus, unauthenticated), and `/work`
(simulated app traffic) stay open.

### Not yet covered

RBAC inside governance-service (who can approve what) is unrelated to this
and already existed — this only gates network access to the HTTP surface.
