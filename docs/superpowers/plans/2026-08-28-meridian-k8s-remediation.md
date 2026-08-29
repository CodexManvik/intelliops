# Meridian Real Remediation via kind — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the 4 Meridian services into the kind cluster as real k8s Deployments so IntelliOps can *really* remediate them (approve a fix → a real pod restart/scale, verified by a real health check), instead of dry-run. The names align so `resolve_target` (which uses the telemetry `service` label as the deployment name) hits the right pod.

**Architecture:** New plain manifests in `deploy/k8s/meridian/` (mirror `demo-app/`), each Deployment/Service named `meridian-<svc>` to match the Prometheus `service` label and `resolve_target`'s output. In-cluster Prometheus scrapes them. A config switch (`meridian_ops_target_mode`) lets the compose gateway's ops-proxy inject faults into the in-cluster pods via NodePort + `host.docker.internal`. `kind-up.sh` applies Meridian; the k8s overlay wires the gateway to k8s ops mode. No engine/logic change — the design aligns names to what `resolve_target`/`k8s_remediator`/`k8s_health` already expect.

**Tech Stack:** Kubernetes (kind) + kubectl; Python 3.11 + FastAPI + Pydantic (the config switch); docker-compose overlay. Gates: `uv run pytest -m "not postgres and not kafka"`, `ruff check .`, `ruff format --check .`, both frontend builds, `kubectl apply --dry-run=client`, `docker compose config`.

**Spec:** `docs/superpowers/specs/2026-08-28-meridian-k8s-remediation-design.md`

## Global Constraints

- **No engine/logic change.** `services/action/targets.py`, `k8s_remediator.py`, `k8s_health.py` are UNCHANGED — the design aligns names to them. If a task proposes changing remediation logic, stop and re-read the spec.
- **The default (non-k8s) stack is untouched.** `meridian_ops_target_mode` defaults to `"compose"` (today's behavior). Base compose + tests + CI never run k8s mode. Follows ADR-012 (config switch, test-safe default).
- **Name alignment is load-bearing:** every Meridian k8s Deployment/Service is named exactly `meridian-gateway` / `meridian-validation` / `meridian-aggregation` / `meridian-reporting` — matching the Prometheus `service` label and `resolve_target`'s output. Do NOT deviate.
- **NodePorts:** gateway 30808, validation 30811, aggregation 30812, reporting 30813 (each mapped in kind-config). k8s-mode gateway targets `host.docker.internal:<nodeport>`.
- **Test-safe:** existing suite (~431) stays green; the one new unit test (ops-target-url) makes it ~432. `ruff` clean; both frontends build.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/meridian-k8s-remediation` (off master). Open a PR; the user merges.
- **Cannot verify live k8s here.** No kind cluster in this environment. Verify everything else (kubectl dry-run for manifest validity, the config-switch unit test, compose-config parse, full suite). The live e2e is a documented manual step (Task 5).
- **kubectl availability:** if `kubectl` isn't on PATH in the task environment, the `--dry-run=client` checks can't run — in that case the implementer notes it and relies on YAML-validity (a `python -c "import yaml; yaml.safe_load_all(...)"` parse) instead, flagging that kubectl dry-run is deferred to the user's machine.

---

## Task 1: Config-switched fault path (gateway `_ops_target_url` + setting + test)

**Files:**
- Modify: `common/config.py` (add `meridian_ops_target_mode`)
- Modify: `services/meridian/gateway/app.py` (add `_ops_target_url` helper; use it in `/api/ops/fault` + `/api/ops/clear`)
- Test: `services/meridian/tests/test_ops_target_url.py` (new)

**Interfaces:**
- Produces: `_ops_target_url(svc: str, path: str) -> str` returning `http://meridian-{svc}:8000/{path}` in `compose` mode (default), `http://host.docker.internal:{nodeport}/{path}` in `k8s` mode. Setting `meridian_ops_target_mode: str = "compose"`.

- [ ] **Step 1: Write the failing test**

Create `services/meridian/tests/test_ops_target_url.py`. Test the URL helper in both modes. Because the gateway module has the gauge-registry hazard (see `test_gateway.py`), test the helper in isolation — it should be a module-level function (or importable) that takes the mode explicitly OR reads settings. **Design decision for the implementer:** make `_ops_target_url` take the mode/settings so it's pure and testable WITHOUT importing the full app (avoids the gauge hazard). E.g. `_ops_target_url(svc, path, mode)`.

```python
"""The ops-proxy fault/clear URL switches between compose service DNS and the
in-cluster NodePort (via host.docker.internal) based on meridian_ops_target_mode."""

from __future__ import annotations

from services.meridian.gateway.ops_target import ops_target_url


def test_compose_mode_targets_service_dns():
    assert ops_target_url("aggregation", "admin/fault", "compose") == (
        "http://meridian-aggregation:8000/admin/fault"
    )


def test_k8s_mode_targets_nodeport_via_host_docker_internal():
    # gateway 30808, validation 30811, aggregation 30812, reporting 30813
    assert ops_target_url("aggregation", "admin/fault", "k8s") == (
        "http://host.docker.internal:30812/admin/fault"
    )
    assert ops_target_url("validation", "admin/clear", "k8s") == (
        "http://host.docker.internal:30811/admin/clear"
    )
    assert ops_target_url("gateway", "admin/fault", "k8s") == (
        "http://host.docker.internal:30808/admin/fault"
    )
    assert ops_target_url("reporting", "admin/clear", "k8s") == (
        "http://host.docker.internal:30813/admin/clear"
    )
```

(Put the pure helper in a NEW small module `services/meridian/gateway/ops_target.py` so the test imports it WITHOUT importing `app.py` — this sidesteps the gauge-registry hazard entirely. The gateway's `app.py` imports the helper from there.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest services/meridian/tests/test_ops_target_url.py -v`
Expected: FAIL (module/function doesn't exist).

- [ ] **Step 3: Create the pure helper module**

Create `services/meridian/gateway/ops_target.py`:

```python
"""Ops-proxy target-URL resolution for Meridian fault injection.

In `compose` mode the gateway reaches the sibling Meridian services by their
compose service DNS name. In `k8s` mode the Meridian services run in the kind
cluster and are reached via their NodePorts through host.docker.internal (the
same path the k8s overlay uses to reach the in-cluster Prometheus at :30090).
Kept as a pure function so it's unit-testable without importing the gateway app
(which re-registers prometheus_client gauges — see test_gateway.py)."""

from __future__ import annotations

# service short-name -> NodePort (must match deploy/k8s/meridian/*-service.yaml
# and deploy/k8s/kind-config.yaml extraPortMappings).
_NODEPORTS = {
    "gateway": 30808,
    "validation": 30811,
    "aggregation": 30812,
    "reporting": 30813,
}


def ops_target_url(svc: str, path: str, mode: str) -> str:
    """Build the fault/clear URL for a Meridian service. `path` is e.g.
    'admin/fault'. `mode` is 'compose' (default) or 'k8s'."""
    if mode == "k8s":
        port = _NODEPORTS[svc]
        return f"http://host.docker.internal:{port}/{path}"
    return f"http://meridian-{svc}:8000/{path}"
```

- [ ] **Step 4: Add the config setting**

In `common/config.py`, add near the other meridian/service settings (e.g. after the governance/telemetry block):

```python
    meridian_ops_target_mode: str = "compose"  # "compose" | "k8s"
```

- [ ] **Step 5: Use the helper in the gateway ops-proxy**

In `services/meridian/gateway/app.py`, import the helper + settings, and replace the hardcoded URLs in `/api/ops/fault` and `/api/ops/clear`:

```python
from common.config import get_settings
from services.meridian.gateway.ops_target import ops_target_url
```

In `ops_fault`:
```python
        svc = _known_service(body["service"])
        spec = body["spec"]
        url = ops_target_url(svc, "admin/fault", get_settings().meridian_ops_target_mode)
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url, json=spec)
        return {"status": r.status_code}
```

In `ops_clear`:
```python
        svc = _known_service(body["service"])
        url = ops_target_url(svc, "admin/clear", get_settings().meridian_ops_target_mode)
        with httpx.Client(timeout=5.0) as c:
            r = c.post(url)
        return {"status": r.status_code}
```

(`get_settings` may already be imported from Task 1 of the prior effort — check; don't double-import.)

- [ ] **Step 6: Run the test + full suite**

Run: `uv run pytest services/meridian/tests/test_ops_target_url.py -v` (PASS), then `uv run pytest -m "not postgres and not kafka" -q` (~432 = 431 + the new test file's cases counted as tests), then `ruff check . && ruff format --check .`.
Expected: green. The existing gateway tests still pass (compose mode is the default → the URLs are byte-identical to before for the default path).

- [ ] **Step 7: Commit**

```bash
git add common/config.py services/meridian/gateway/ops_target.py services/meridian/gateway/app.py services/meridian/tests/test_ops_target_url.py
git commit -m "feat(meridian): config-switched ops-proxy target (compose DNS vs k8s NodePort)"
```

---

## Task 2: Meridian k8s manifests + kind-config NodePort mappings

**Files:**
- Create: `deploy/k8s/meridian/gateway.yaml`, `validation.yaml`, `aggregation.yaml`, `reporting.yaml` (each a Deployment + NodePort Service, multi-doc)
- Modify: `deploy/k8s/kind-config.yaml` (add the 4 NodePort mappings)
- Test: `kubectl apply --dry-run=client` (or YAML-parse if kubectl unavailable)

**Interfaces:** none (manifests). Deployment/Service names `meridian-<svc>` — load-bearing.

- [ ] **Step 1: Create the 4 manifests**

Each file (e.g. `deploy/k8s/meridian/aggregation.yaml`) is a Deployment + a NodePort Service, mirroring `demo-app/deployment.yaml` + `demo-app/service.yaml` but named `meridian-<svc>`, pointing `SERVICE_MODULE` at the meridian app, port 8000, with a NodePort. Template (aggregation shown; repeat for gateway/validation/reporting with the right module + nodePort):

```yaml
# deploy/k8s/meridian/aggregation.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: meridian-aggregation
  namespace: intelliops-demo
spec:
  replicas: 1
  selector:
    matchLabels: { app: meridian-aggregation }
  template:
    metadata:
      labels: { app: meridian-aggregation }
    spec:
      containers:
        - name: meridian-aggregation
          image: intelliops-demo-app:local
          imagePullPolicy: IfNotPresent
          env:
            - { name: SERVICE_MODULE, value: "services.meridian.aggregation.app:app" }
            - { name: PORT, value: "8000" }
          ports: [{ containerPort: 8000 }]
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 2
            periodSeconds: 3
          resources:
            requests: { cpu: "50m", memory: "64Mi" }
            limits: { cpu: "250m", memory: "128Mi" }
---
apiVersion: v1
kind: Service
metadata:
  name: meridian-aggregation
  namespace: intelliops-demo
  labels: { app: meridian-aggregation }
spec:
  type: NodePort
  selector: { app: meridian-aggregation }
  ports:
    - port: 8000
      targetPort: 8000
      nodePort: 30812
```

Per-service values:
- `gateway.yaml`: module `services.meridian.gateway.app:app`, nodePort **30808**. (Note: the gateway also serves the UI + ops-proxy, but in k8s mode the UI/ops-proxy still runs in the COMPOSE gateway — this in-cluster gateway is just the faultable workload/metrics source. That's fine; it exposes the same `/admin/fault` + `/metrics`.)
- `validation.yaml`: module `services.meridian.validation.app:app`, nodePort **30811**.
- `aggregation.yaml`: module `services.meridian.aggregation.app:app`, nodePort **30812**.
- `reporting.yaml`: module `services.meridian.reporting.app:app`, nodePort **30813**.

- [ ] **Step 2: Add NodePort mappings to kind-config**

In `deploy/k8s/kind-config.yaml`, add to `extraPortMappings` (after the 30090 prometheus entry):

```yaml
      - containerPort: 30808   # meridian-gateway NodePort
        hostPort: 30808
        protocol: TCP
      - containerPort: 30811   # meridian-validation NodePort
        hostPort: 30811
        protocol: TCP
      - containerPort: 30812   # meridian-aggregation NodePort
        hostPort: 30812
        protocol: TCP
      - containerPort: 30813   # meridian-reporting NodePort
        hostPort: 30813
        protocol: TCP
```

- [ ] **Step 3: Verify manifest validity**

If `kubectl` is available: `kubectl apply --dry-run=client -f deploy/k8s/meridian/` (should report all 8 objects valid — needs no cluster for client dry-run). Also `kubectl apply --dry-run=client -f deploy/k8s/kind-config.yaml` isn't applicable (kind-config isn't a k8s object) — instead validate its YAML parses.
If `kubectl` is NOT available: validate all YAML parses — `python -c "import yaml,glob; [list(yaml.safe_load_all(open(f))) for f in glob.glob('deploy/k8s/meridian/*.yaml')]; list(yaml.safe_load_all(open('deploy/k8s/kind-config.yaml'))); print('yaml ok')"`. Note in the report which check ran.

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/meridian/ deploy/k8s/kind-config.yaml
git commit -m "feat(k8s): Meridian Deployments + NodePort Services (aligned names for real remediation)"
```

---

## Task 3: In-cluster Prometheus scrape jobs + broadened k8s-mode ingestion query

**Files:**
- Modify: `deploy/k8s/prometheus/configmap.yaml` (add 4 Meridian scrape jobs)
- Modify: `deploy/docker-compose.k8s.yml` (ensure ingestion's query includes Meridian metrics)
- Test: YAML validity

**Interfaces:** the scrape `service` labels MUST equal the Deployment names (`meridian-<svc>`).

- [ ] **Step 1: Add the 4 Meridian scrape jobs**

In `deploy/k8s/prometheus/configmap.yaml`, add under `scrape_configs` (after the `demo-app` job):

```yaml
      - job_name: meridian-gateway
        static_configs:
          - targets: ["meridian-gateway.intelliops-demo:8000"]
            labels:
              service: meridian-gateway
      - job_name: meridian-validation
        static_configs:
          - targets: ["meridian-validation.intelliops-demo:8000"]
            labels:
              service: meridian-validation
      - job_name: meridian-aggregation
        static_configs:
          - targets: ["meridian-aggregation.intelliops-demo:8000"]
            labels:
              service: meridian-aggregation
      - job_name: meridian-reporting
        static_configs:
          - targets: ["meridian-reporting.intelliops-demo:8000"]
            labels:
              service: meridian-reporting
```

- [ ] **Step 2: Broaden the k8s-mode ingestion query**

Check what `deploy/docker-compose.k8s.yml`'s ingestion override sets. The base `docker-compose.yml` ingestion sets `INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'`, but the overlay currently only overrides `INTELLIOPS_PROMETHEUS_URL` on ingestion (it inherits the base query via compose merge — VERIFY this: compose `environment` maps DO merge key-by-key across overlay files, so the base query IS inherited unless the overlay clears it). If the base query is inherited, NO change is needed here — confirm by `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml config | grep -A2 "ingestion" | grep QUERY`. If the query is NOT present in the resolved config, add `INTELLIOPS_PROMETHEUS_QUERY: '{__name__=~"cpu_usage|meridian_error_rate"}'` to the overlay's ingestion env. Report which case held.

- [ ] **Step 3: Verify YAML + compose config**

Run: `python -c "import yaml; yaml.safe_load(open('deploy/k8s/prometheus/configmap.yaml')); print('prom yaml ok')"` and `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml config >/dev/null && echo "overlay config valid"` (if Docker available; else note deferred).

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/prometheus/configmap.yaml deploy/docker-compose.k8s.yml
git commit -m "feat(k8s): Prometheus scrapes Meridian services; k8s ingestion sees Meridian metrics"
```

---

## Task 4: kind-up applies Meridian + overlay wires the gateway to k8s ops mode

**Files:**
- Modify: `scripts/kind-up.sh` (apply Meridian + rollout waits)
- Modify: `deploy/docker-compose.k8s.yml` (meridian-gateway override: k8s ops mode + host.docker.internal)
- Test: bash syntax + compose config

**Interfaces:** none.

- [ ] **Step 1: kind-up applies Meridian**

In `scripts/kind-up.sh`, after `kubectl apply -f "$HERE/deploy/k8s/demo-app/"` and the prometheus apply, add:

```bash
kubectl apply -f "$HERE/deploy/k8s/meridian/"
```

And after the existing rollout-status waits, add waits for the 4 Meridian deployments:

```bash
for svc in gateway validation aggregation reporting; do
  kubectl -n intelliops-demo rollout status deploy/meridian-$svc --timeout=120s
done
```

Update the echo'd summary to mention Meridian is now in-cluster + the NodePorts (30808/30811/30812/30813).

- [ ] **Step 2: Overlay wires the gateway to k8s ops mode**

In `deploy/docker-compose.k8s.yml`, add a `meridian-gateway` service override:

```yaml
  meridian-gateway:
    environment:
      INTELLIOPS_MERIDIAN_OPS_TARGET_MODE: k8s
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

(This makes the compose gateway — still serving the UI — inject faults into the in-cluster Meridian pods via the NodePorts. The gateway stays in compose; only its fault TARGET moves to the cluster.)

- [ ] **Step 3: Verify**

Run: `bash -n scripts/kind-up.sh && echo "kind-up syntax ok"` and `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.k8s.yml config >/dev/null && echo "overlay valid"` (confirm the meridian-gateway override resolves with `INTELLIOPS_MERIDIAN_OPS_TARGET_MODE: k8s`).

- [ ] **Step 4: Commit**

```bash
git add scripts/kind-up.sh deploy/docker-compose.k8s.yml
git commit -m "feat(k8s): kind-up deploys Meridian; overlay puts the gateway in k8s ops mode"
```

---

## Task 5: Docs + final verification + TODO update

**Files:**
- Modify: `deploy/k8s/README.md` (Meridian-on-kind section)
- Modify: `TODO.md` (mark the HIGH k8s item done)
- Test: all gates

- [ ] **Step 1: Extend the k8s README**

Add a "Real remediation on Meridian" section to `deploy/k8s/README.md` (after the existing demo-app flow). Cover: `./scripts/kind-up.sh` now brings up Meridian too; export the kubeconfig (existing step); start the stack with the overlay (now sets the gateway to k8s ops mode); **break a Meridian service from the Operations UI** at `http://localhost:8008` (the "Break aggregation" preset now hits the in-cluster pod via NodePort) OR via `kubectl -n intelliops-demo exec deploy/meridian-aggregation -- python -c "..."`; watch IntelliOps detect→diagnose→gate in the console; **Approve**; watch the real pod act with `kubectl -n intelliops-demo get pods -w`. Document: `restart-pod` is the clean-success path (rollout restart → fresh in-process MeridianState → fault cleared → healthy); `scale-service` may roll back (the reversible-only safety property working, same as demo-app §4). Include the exact commands.

- [ ] **Step 2: Update TODO.md**

Mark the "HIGH — Real remediation against Meridian (deploy Meridian into k8s)" entry as **DONE / shipped in feat/meridian-k8s-remediation**, with a one-line note on the approach (Meridian Deployments in kind, name-aligned to resolve_target, gateway ops-proxy switched to NodePort in k8s mode).

- [ ] **Step 3: All gates**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build && npm --prefix services/meridian/ui run build`
Expected: ~432 pass, ruff clean, both builds clean. (No frontend change in this effort, but confirm nothing broke.)

- [ ] **Step 4: Verify base (non-k8s) behavior unchanged**

Confirm the base compose is untouched behaviorally: `docker compose -f deploy/docker-compose.yml config | grep MERIDIAN_OPS_TARGET_MODE` should show NOTHING (the setting defaults to `compose` and isn't set in the base) — so the default demo is byte-identical. Report this.

- [ ] **Step 5: Commit**

```bash
git add deploy/k8s/README.md TODO.md
git commit -m "docs: Meridian-on-kind real-remediation flow; mark k8s remediation shipped"
```

---

## Self-Review checklist (before the PR)

1. **Config switch:** `meridian_ops_target_mode` defaults to `compose` (suite green, base demo unchanged); `k8s` mode targets the NodePorts — unit-tested — Task 1.
2. **Name alignment:** every Meridian Deployment/Service = `meridian-<svc>` = the Prometheus `service` label = `resolve_target`'s output — Tasks 2, 3.
3. **Manifests valid:** kubectl dry-run (or YAML parse) passes; kind-config maps the 4 NodePorts — Task 2.
4. **Prometheus + ingestion see Meridian:** 4 scrape jobs with the right labels; k8s ingestion query includes the Meridian metrics — Task 3.
5. **kind-up + overlay wire it:** kind-up applies Meridian + waits; overlay sets the gateway to k8s ops mode + host.docker.internal — Task 4.
6. **Docs + gates:** README flow documented; suite ~432 + ruff + both builds green; base compose behavior unchanged — Task 5.
7. **(Manual, user's machine) live e2e:** break Meridian → detect → diagnose → approve → real pod restart → healthy. Documented in the README; NOT verifiable in CI.
