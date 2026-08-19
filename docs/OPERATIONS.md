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
route on ingestion, correlation, rca, action, feedback, and read — except
`/health`. In practice the endpoints this actually protects are the ones
with real external read/write surface:

- **read-service** — `/situations`, `/outcomes`, `/metrics`, `/reset`
- **governance-service** (external / frontend) — `GET /audit`,
  `GET /playbooks`, `GET /approvals`, `POST /approvals/{id}/decide`
- **correlation-service** — `/reset-baseline` (simulation control)

demo-app doesn't use the shared factory (it's an external target, not an
IntelliOps service), so it's gated per-route instead: `/break` and `/fix`
(simulation controls) require the token in `token` mode; `/health`,
`/metrics` (scraped by Prometheus, unauthenticated), and `/work`
(simulated app traffic) stay open.

### Internal service-to-service exemptions

Governance hosts endpoints used by both external clients (the React
console) and internal services (action, feedback) over the Docker-compose
internal network.  The following internal-bus endpoints are exempt from
auth — they are never exposed outside compose:

| Path | Caller | Purpose |
| --- | --- | --- |
| `POST /rbac/check` | action (HttpGovernanceGate) | RBAC permission check |
| `POST /audit` | action (HttpGovernanceGate) | Write audit record |
| `POST /approvals` | action (HttpGovernanceGate) | Create approval request |
| `GET /approvals/{id}` | action (HttpGovernanceGate) | Poll approval status |
| `POST /playbooks/{id}/graduate` | feedback | Promote playbook hitl→auto |

This is configured via the `auth_exempt` callback in `create_app()` — see
`services/governance/app.py::_governance_exempt`.

### Not yet covered

RBAC inside governance-service (who can approve what) is unrelated to this
and already existed — this only gates network access to the HTTP surface.
