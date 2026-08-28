# Meridian Real Remediation via kind — Design Spec

**Date:** 2026-08-28
**Owner:** Manvik
**Status:** design (architectural — new k8s manifests, cluster topology, a config-switched fault path, target-resolution alignment).

## The problem

Today there are two disjoint demos: **Meridian** (docker-compose) gives the best detect/diagnose story but remediation is `dry_run` (logs steps, simulated healthy, touches nothing); the **kind cluster** (`deploy/k8s/`) gives *real* pod remediation via the Kubernetes API but only against a single `demo-app`, not Meridian. The goal: **real remediation on Meridian** — approving a fix really restarts/scales a Meridian pod, verified by a real health check.

## The linchpin (verified against code)

`services/action/targets.py:resolve_target` derives the k8s deployment name from the incident's telemetry `service` label, using that label **as the deployment name directly**. So three names must be identical for remediation to hit the right pod:
1. The Prometheus `service` label (e.g. `meridian-aggregation`)
2. The k8s Deployment name (`meridian-aggregation`)
3. What `action` scales/restarts (it uses the label → deployment name)

The compose Prometheus already labels Meridian metrics `service: meridian-<svc>` (`deploy/prometheus.yml`). So the k8s Deployments MUST be named `meridian-gateway`/`-validation`/`-aggregation`/`-reporting`, and the in-cluster Prometheus scrape config must label them identically.

## Topology (mirrors the existing demo-app path)

- **In the kind cluster** (`intelliops-demo` namespace): the 4 Meridian services (Deployment + Service each) + Prometheus scraping them + the existing demo-app. These are the workloads IntelliOps remediates.
- **In docker-compose** (via `docker-compose.k8s.yml` overlay): the IntelliOps services, with `action` in `k8s` mode targeting the cluster (already wired for demo-app; extend to Meridian).

## Key decisions (locked)

1. **All 4 Meridian services** deployed into kind.
2. **Gateway ops-proxy reaches the cluster** so the Operations "Break" button works in k8s mode — via **NodePort + `host.docker.internal:<nodeport>`** (mirrors how the overlay reaches Prometheus at `:30090`). NOT pod-DNS.
3. **Plain manifests** in `deploy/k8s/meridian/` (mirror `deploy/k8s/demo-app/`), applied by `kind-up.sh`.
4. **`restart-pod` is the guaranteed clean-success path** (rollout restart → fresh in-process `MeridianState` → fault cleared → healthy). `scale-service` may roll back (documented — same caveat as demo-app, and a good "reversible-only safety working" story).

## Non-goals / constraints

- **No engine/logic change.** `resolve_target`, `k8s_remediator`, `k8s_health` are unchanged — the design ALIGNS names to what they already expect.
- **The default (non-k8s) stack is untouched.** The base compose + tests + CI never run k8s mode; `MERIDIAN_OPS_TARGET_MODE` defaults to `compose` (today's behavior). This follows ADR-012 (config switch, test-safe default).
- **No new heavy dependency.**
- **I cannot fully verify the live k8s flow** (needs kind + kubectl + a manually-created cluster). The spec includes the manual verification steps; everything verifiable without a cluster (YAML validity, `kubectl --dry-run=client`, the config-switch unit test, compose-overlay parse, full pytest suite) IS verified in-task.

## Global Constraints

- **Test-safe:** the existing suite (`uv run pytest -m "not postgres and not kafka"`, ~431) stays green — the fault-path config switch has a `compose` default preserving today's behavior. `ruff check .` + `ruff format --check .` clean. Both frontends still build.
- **No fabricated data.**
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Shared files:** `services/meridian/gateway/app.py` (fault-path switch), `common/config.py` (new setting), `deploy/k8s/*` (manifests + scrape), `scripts/kind-up.sh`, `deploy/docker-compose.k8s.yml`, `deploy/k8s/README.md`.

---

## Design

### 1. Meridian k8s manifests (`deploy/k8s/meridian/`)

One Deployment + one Service per Meridian service (4 total, or one multi-doc file per service). Each mirrors `demo-app/deployment.yaml`:
- **Deployment** named `meridian-<svc>` in `intelliops-demo`, image `intelliops-demo-app:local` (the shared image — same one demo-app uses; `kind-up` loads it), env `SERVICE_MODULE=services.meridian.<svc>.app:app`, `PORT=8000`, containerPort 8000. Readiness probe `GET /health` on 8000. Resource requests/limits (e.g. requests cpu 50m/mem 64Mi, limits cpu 250m/mem 128Mi) so scale is meaningful.
- **Service** named `meridian-<svc>`, selector `app: meridian-<svc>`, `type: NodePort`, port 8000 → a fixed nodePort. Suggested nodePorts (must be in kind-config's mapped range — VERIFY against `deploy/k8s/kind-config.yaml`, which maps 30090 for Prometheus; add mappings for these): gateway 30808, validation 30811, aggregation 30812, reporting 30813.
- **kind-config.yaml:** add `extraPortMappings` for each new nodePort (mirror the existing 30090 mapping) so the host can reach them.

**Label alignment:** the Deployment `metadata.name` = `meridian-<svc>` = the Prometheus `service` label = `resolve_target`'s output. This is the whole point — do not deviate from these names.

### 2. In-cluster Prometheus scrape (`deploy/k8s/prometheus/configmap.yaml`)

Add 4 scrape jobs (mirror the existing `demo-app` job), each targeting `meridian-<svc>.intelliops-demo:8000` with a static label `service: meridian-<svc>`. This makes the in-cluster Prometheus (which the k8s-mode ingestion scrapes) see the Meridian metrics with the right `service` label.

Also: the k8s-mode **ingestion query** must include the Meridian metric names. Today the base compose sets `INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'` on ingestion, but the k8s overlay may override/rely on the default (`cpu_usage`). Ensure the k8s overlay's ingestion also uses the broadened query so Meridian's `cpu_usage`/`meridian_error_rate` reach correlation. (Verify what the overlay sets; add the broadened query to the overlay's ingestion env if missing.)

### 3. Config-switched fault path (gateway)

Add a setting `meridian_ops_target_mode: str = "compose"` to `common/config.py` (`"compose"` | `"k8s"`). In `services/meridian/gateway/app.py`, the ops-proxy (`/api/ops/fault`, `/api/ops/clear`) builds its target URL from this setting:
- `compose` (default, today): `http://meridian-{svc}:8000/admin/{fault|clear}`
- `k8s`: `http://host.docker.internal:{nodeport}/admin/{fault|clear}` — a fixed `svc → nodeport` map in the gateway (gateway 30808, validation 30811, aggregation 30812, reporting 30813).

Factor the URL construction into a small helper (`_ops_target_url(svc, path)`) so both fault and clear use it, and it's unit-testable. A test asserts: compose mode → the `meridian-{svc}:8000` URL; k8s mode → the `host.docker.internal:{nodeport}` URL. (This is the one code change with a real test; the manifests are verified via kubectl dry-run.)

### 4. docker-compose k8s overlay (`deploy/docker-compose.k8s.yml`)

Add a `meridian-gateway` override that sets `INTELLIOPS_MERIDIAN_OPS_TARGET_MODE: k8s` + `extra_hosts: host.docker.internal:host-gateway` (so the gateway can reach the NodePorts). This makes the compose gateway (still serving the UI) inject faults into the in-cluster pods. (The gateway stays in compose; only its fault TARGET changes to the cluster NodePorts.)

Note the resulting split in k8s mode: the Meridian *pods* run in kind (remediable), but the Meridian *gateway UI* still runs in compose (serving the Operations panel + proxying faults to the cluster). That's fine and intended — the gateway is the control surface, the pods are the workload. (Alternatively the gateway could also run in-cluster, but keeping it in compose preserves the existing UI-serving setup with the least change.)

### 5. kind-up.sh

After the demo-app apply, add: build is already the shared image (no new build — Meridian uses the same `intelliops-demo-app:local`), `kubectl apply -f deploy/k8s/meridian/`, and `kubectl rollout status` for each of the 4 Meridian deployments. Update the echo'd next-steps.

### 6. Docs (`deploy/k8s/README.md`)

Add a "Real remediation on Meridian" section: bring up the cluster (now includes Meridian), start the compose stack with the overlay (now sets the gateway to k8s ops mode), break a Meridian service **from the Operations UI** (works now via the NodePort path) OR via kubectl, watch IntelliOps detect→diagnose→gate, approve, and watch `kubectl -n intelliops-demo get pods -w` show the real pod restart/scale. Document the `restart-pod` clean-success path and the `scale-service` rollback caveat.

---

## Acceptance criteria

1. **Manifests valid + aligned:** `deploy/k8s/meridian/` has 4 Deployments + 4 NodePort Services named `meridian-<svc>`; `kubectl apply --dry-run=client -f deploy/k8s/meridian/` passes; the names match the Prometheus `service` labels and `resolve_target`'s output.
2. **In-cluster Prometheus** scrapes all 4 Meridian services with `service: meridian-<svc>` labels; k8s-mode ingestion query includes the Meridian metrics.
3. **Config-switched fault path:** `meridian_ops_target_mode` defaults to `compose` (today's behavior, suite green); `k8s` mode targets `host.docker.internal:<nodeport>`. Unit-tested both modes.
4. **Overlay wires the gateway to k8s ops mode** + host.docker.internal; parses (`docker compose -f ... -f ... config` valid).
5. **kind-up applies Meridian + waits for rollouts.**
6. **Docs** describe the full Meridian-on-kind real-remediation flow.
7. **Test-safe:** existing suite ~432 green (431 + the new ops-target-url test); ruff clean; both frontends build; base (non-k8s) compose unchanged in behavior.
8. **(Manual, documented — not CI)** the end-to-end flow verified on a real kind cluster by the user: break Meridian → detect → diagnose → approve → real pod restart → healthy.

## Suggested task ordering (for the plan)

1. `meridian_ops_target_mode` config setting + the gateway `_ops_target_url` helper (config-switched fault/clear URL) + unit test (compose + k8s modes). The one code change; keeps the suite green.
2. `deploy/k8s/meridian/` manifests (4 Deployment+NodePort-Service pairs, aligned names) + kind-config nodePort mappings. Verify `kubectl apply --dry-run=client`.
3. In-cluster Prometheus scrape jobs for the 4 Meridian services + the broadened k8s-mode ingestion query.
4. `kind-up.sh` applies Meridian + rollout waits; `docker-compose.k8s.yml` gateway override (k8s ops mode + host.docker.internal).
5. `deploy/k8s/README.md` Meridian-on-kind section; final gates (suite, ruff, builds, compose config, kubectl dry-run).

Rationale: the code+config change first (keeps tests green, is the only unit-testable piece), then the manifests, then the wiring, then docs — each independently verifiable without a live cluster except the final manual e2e.
