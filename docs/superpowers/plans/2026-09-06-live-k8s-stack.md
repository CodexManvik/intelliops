# Live Kubernetes Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the entire IntelliOps platform (7 services + Meridian + demo-app + Postgres + Redis + Prometheus + React console) to Kubernetes via one Helm release, with embedding runbook selection + LLM explanations live, real k8s remediation, and per-metric health verification against real Prometheus.

**Architecture:** Extend the existing `deploy/k8s/platform/` Helm chart: per-service image targets (rca+action → the `full` ml/k8s image), an AI/mode env surface driven by a `values-live.yaml` overlay (defaults stay safe), a ServiceAccount+ClusterRole+ClusterRoleBinding for the action service, chart-managed demo-app/Meridian/Prometheus, an LLM Secret, and an nginx-served frontend that same-origin-proxies the read API (SSE-safe). One small code change: the action k8s client picks `load_incluster_config()` in-pod. A `kind-up-full.sh` script + README document the bring-up.

**Tech Stack:** Helm 3, Kubernetes (kind for local), Docker multi-stage (uv, CPU-only torch), nginx, Python 3.11 (kubernetes client), React/Vite (existing console).

**Spec:** docs/superpowers/specs/2026-09-06-live-k8s-stack-design.md

## Global Constraints

- **Safe-by-default:** the chart's DEFAULT `values.yaml` keeps today's safe posture (REMEDIATOR_MODE dry-run, RUNBOOK_SELECTOR_MODE off, LLM template, HEALTH_CHECK_MODE always, no RBAC). The LIVE posture is strictly opt-in via `values-live.yaml`. A plain `helm install` must never touch a real cluster or require a key.
- **The LLM API key is the operator's** — NEVER committed, NEVER placed in the configmap or a values file in git, NEVER handled by the implementer. It enters at install via `--set` or a pre-created Secret referenced by name. Every doc shows the command; no key value is ever written to a file.
- **CPU-only torch:** the `full` image pins the CPU torch wheel (no CUDA). Inference only.
- **Slim-boundary preserved:** the `base` image stays ml/k8s-free (11 services). Only rca+action use `full`. The code slim-boundary guard is untouched.
- **The metrics-arc app logic is DONE and unchanged** — this plan is deploy/infra + ONE small in-cluster-auth code change. Do not modify detection/RCA/action/verification logic or the frontend app code (only add the frontend's k8s packaging).
- **Validation bar:** every chart change is validated with `helm lint` + `helm template` (rendered output asserted). `kubectl --dry-run=client` where a cluster isn't available. A full live run only if the environment supports Docker+kind+Helm; otherwise deliver validated configs + a precise runbook, and never claim "verified live" for something that didn't run.
- Every commit ends with the trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Helm/kubectl commands run from the repo root; the chart is `deploy/k8s/platform`.

---

### Task 1: In-cluster auth for the action k8s client (the only code change)

**Files:**
- Modify: `services/action/adapters/k8s_health.py` (its `_default_apps_v1`), `services/action/adapters/k8s_remediator.py`, `services/action/adapters/sandbox.py` — wherever `config.load_kube_config()` is called.
- Create: `services/action/adapters/kube_config.py` (a shared loader) — OR add a small helper; the implementer picks the DRY option.
- Test: `services/action/tests/test_kube_config.py` (new) or extend an existing action test.

**Interfaces:**
- Produces: a single function `load_kube(config, os_environ=os.environ) -> None` (or similar) that calls `config.load_incluster_config()` when running in a pod (detected via `KUBERNETES_SERVICE_HOST` in the environment) and `config.load_kube_config()` otherwise. All three adapters call this instead of `config.load_kube_config()` directly.

- [ ] **Step 1: Find every `load_kube_config` call**

Run: `grep -rn "load_kube_config\|load_incluster_config" services/action/`
Expected: the current calls (in the lazy client factories). Note each.

- [ ] **Step 2: Write the failing test**

```python
# services/action/tests/test_kube_config.py
from services.action.adapters.kube_config import load_kube


class _FakeConfig:
    def __init__(self):
        self.calls = []
    def load_incluster_config(self):
        self.calls.append("incluster")
    def load_kube_config(self):
        self.calls.append("kubeconfig")


def test_in_pod_uses_incluster():
    cfg = _FakeConfig()
    load_kube(cfg, {"KUBERNETES_SERVICE_HOST": "10.0.0.1"})
    assert cfg.calls == ["incluster"]


def test_out_of_pod_uses_kubeconfig():
    cfg = _FakeConfig()
    load_kube(cfg, {})
    assert cfg.calls == ["kubeconfig"]
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest services/action/tests/test_kube_config.py -v`
Expected: FAIL — `services.action.adapters.kube_config` does not exist.

- [ ] **Step 4: Implement `kube_config.py`**

```python
"""Pick the right kube auth: in-cluster ServiceAccount when running as a pod,
a kubeconfig file otherwise (compose/local). Kept tiny + injectable so the
selection is unit-testable without a real client or cluster."""

from __future__ import annotations

import os


def load_kube(config, os_environ: dict | None = None) -> None:
    env = os.environ if os_environ is None else os_environ
    if env.get("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
    else:
        config.load_kube_config()
```

Then in each adapter's lazy client factory, replace `config.load_kube_config()` with:
```python
from services.action.adapters.kube_config import load_kube
# ...
from kubernetes import client, config
load_kube(config)
return client.AppsV1Api()   # (or CoreV1Api, as the factory does)
```
Keep the `from kubernetes import ...` LAZY (inside the factory) exactly as today — slim-boundary. Do not change any other logic.

- [ ] **Step 5: Run tests + full suite + lint + slim**

Run: `uv run pytest services/action/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && uv run ruff check . && uv run ruff format --check . && uv run python -c "import sys; import services.action.app; print('kubernetes' in sys.modules)"`
Expected: green; slim check `False` (kubernetes still lazy — the loader import is inside the factory, not module-top). The full suite count is unchanged +2 (the two new tests). The compose k8s path is unaffected (out-of-pod → kubeconfig, exactly as before).

- [ ] **Step 6: Commit**

```bash
git add services/action/adapters/kube_config.py services/action/adapters/k8s_health.py services/action/adapters/k8s_remediator.py services/action/adapters/sandbox.py services/action/tests/test_kube_config.py
git commit -m "feat(action): in-cluster kube auth (ServiceAccount in-pod, kubeconfig otherwise)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: The `full` image — CPU-only torch + baked embedding model

**Files:**
- Modify: `deploy/Dockerfile` (the `full` target).

**Interfaces:**
- Produces: a `full` image whose venv has sentence-transformers on **CPU-only torch** and the **all-MiniLM-L6-v2** weights pre-cached, so `RUNBOOK_SELECTOR_MODE=embedding` works air-gapped.

- [ ] **Step 1: Pin CPU-only torch in the `full` target**

In the `full` stage's `uv sync ... --extra ml --extra k8s`, ensure torch resolves to the CPU wheel. Two mechanisms (implementer verifies which the uv/torch versions need):
- Add a `[tool.uv.sources]` / `[[tool.uv.index]]` for the pytorch CPU index in `pyproject.toml` scoped so ONLY the full build uses it, OR
- In the Dockerfile `full` stage, after the ml sync, `RUN uv pip install --python /app/.venv torch --index-url https://download.pytorch.org/whl/cpu` to force the CPU build (overriding any CUDA pull).
Prefer the approach that keeps the base image untouched and the lockfile consistent. Document the chosen mechanism in a Dockerfile comment.

- [ ] **Step 2: Bake the embedding model**

Add to the `full` stage, after the venv is populated:
```dockerfile
# Pre-cache the embedding model so RUNBOOK_SELECTOR_MODE=embedding works with no
# runtime HuggingFace egress. ~90MB, CPU inference. Cached under /root/.cache.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
```
(Ensure the cache path the runtime uses matches — set `HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` consistently if needed, or rely on the default `~/.cache`. The implementer verifies the model loads from cache offline.)

- [ ] **Step 3: Build + verify (if Docker is available)**

Run (if Docker present): `docker build -f deploy/Dockerfile --target full -t intelliops:full-test .`
Then verify offline load: `docker run --rm --network none intelliops:full-test python -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('all-MiniLM-L6-v2'); print(m.encode(['db pool exhausted']).shape)"`
Expected: prints an embedding shape (e.g. `(1, 384)`) with NO network — proving the model is baked and torch is CPU-only. Also check the image isn't CUDA-bloated: `docker images intelliops:full-test` (expect ~1-1.5GB, not ~6GB).
**If Docker is NOT available in this environment:** skip the build; validate the Dockerfile syntax by inspection and record in the report that the build is unverified-in-session, to be run by the operator. Do NOT claim it built if it didn't.

- [ ] **Step 4: Commit**

```bash
git add deploy/Dockerfile pyproject.toml
git commit -m "build: full image on CPU-only torch with baked all-MiniLM embedding model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Per-service image targets + AI/mode env (chart)

**Files:**
- Modify: `deploy/k8s/platform/values.yaml`, `deploy/k8s/platform/templates/service-deployment.yaml`, `deploy/k8s/platform/templates/configmap.yaml`.
- Create: `deploy/k8s/platform/values-live.yaml`.

**Interfaces:**
- Produces: each service entry may set `image: full` (default base); rca+action set `full`. The configmap carries the full `INTELLIOPS_*` env surface, values-driven; `values-live.yaml` sets the live posture.

- [ ] **Step 1: Extend `values.yaml`**

Add to `image:` a full-image ref (e.g. `fullTag` or a separate `fullRepository`), and to each service entry an optional `image: full`. Set `rca` and `action` to `image: full`. Extend `env:` with the full surface at SAFE defaults:
```yaml
env:
  AUTH_MODE: "off"
  STORE_BACKEND: postgres
  BUS_BACKEND: redis
  CORRELATOR_KIND: "river"          # robust in values-live
  DETECTION_POLICY: "off"           # on in values-live
  RUNBOOK_SELECTOR_MODE: "off"      # embedding in values-live
  REMEDIATOR_MODE: "dry_run"        # k8s in values-live
  SANDBOX_MODE: "off"               # k8s in values-live
  HEALTH_CHECK_MODE: "always"       # k8s in values-live
  PROMETHEUS_URL: "http://prometheus:9090"
  LLM_EXPLANATION_ENDPOINT: ""      # set in values-live
  LLM_EXPLANATION_MODEL: ""         # set in values-live
rbac:
  create: false                     # true in values-live
llm:
  apiKeySecretName: ""              # name of a pre-created Secret (or "" to use --set)
```

- [ ] **Step 2: Image selection in the deployment template**

In `service-deployment.yaml`, compute the image per entry:
```yaml
{{- $img := $.Values.image.repository }}
{{- $tag := $.Values.image.tag }}
{{- if eq (.image | default "base") "full" }}
{{- $img = ($.Values.image.fullRepository | default $.Values.image.repository) }}
{{- $tag = ($.Values.image.fullTag | default $.Values.image.tag) }}
{{- end }}
image: "{{ $img }}:{{ $tag }}"
```
(Adjust to the exact fields chosen in Step 1. The migrate Job keeps the base image.)

- [ ] **Step 3: Thread env through the configmap**

Add every `INTELLIOPS_<KEY>: {{ .Values.env.<KEY> | quote }}` line to `configmap.yaml` for the new keys. Do NOT put the LLM API key here.

- [ ] **Step 4: Create `values-live.yaml`**

```yaml
# Opt-in LIVE posture. Enable with: helm install ... -f values-live.yaml
# The LLM API key is NOT here — supply it at install (see README):
#   helm install ... --set-string llm.apiKey=$GROQ_API_KEY
# or pre-create a Secret and set llm.apiKeySecretName.
image:
  fullTag: latest        # or a distinct full-image tag
env:
  CORRELATOR_KIND: "robust"
  DETECTION_POLICY: "on"
  RUNBOOK_SELECTOR_MODE: "embedding"
  REMEDIATOR_MODE: "k8s"
  SANDBOX_MODE: "k8s"
  HEALTH_CHECK_MODE: "k8s"
  LLM_EXPLANATION_ENDPOINT: "https://api.groq.com/openai/v1"
  LLM_EXPLANATION_MODEL: "llama-3.3-70b-versatile"
rbac:
  create: true
```

- [ ] **Step 5: Validate**

Run: `helm lint deploy/k8s/platform` and
`helm template t deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml | grep -E "image:|INTELLIOPS_(RUNBOOK_SELECTOR|REMEDIATOR|HEALTH_CHECK|DETECTION|CORRELATOR)"`
Expected: rca + action Deployments show the full image; the others base; the configmap shows the live env values. Also `helm template t deploy/k8s/platform` (default) shows the SAFE values (dry_run/off/always).

- [ ] **Step 6: Commit**

```bash
git add deploy/k8s/platform/values.yaml deploy/k8s/platform/values-live.yaml deploy/k8s/platform/templates/service-deployment.yaml deploy/k8s/platform/templates/configmap.yaml
git commit -m "feat(helm): per-service images (rca/action full) + AI/mode env via values-live overlay

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: RBAC + in-cluster auth wiring for the action service

**Files:**
- Create: `deploy/k8s/platform/templates/rbac.yaml`.
- Modify: `deploy/k8s/platform/templates/service-deployment.yaml` (set `serviceAccountName` on the action deployment when rbac.create).

**Interfaces:**
- Produces: a ServiceAccount `intelliops-action`, a ClusterRole with exactly the action service's verbs, a ClusterRoleBinding, gated on `.Values.rbac.create`. The action Deployment sets `serviceAccountName: intelliops-action` when rbac is on.

- [ ] **Step 1: Write `rbac.yaml`**

```yaml
{{- if .Values.rbac.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: intelliops-action
  labels:
    {{- include "intelliops.labels" . | nindent 4 }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ .Release.Name }}-intelliops-action
  labels:
    {{- include "intelliops.labels" . | nindent 4 }}
rules:
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list", "create", "patch"]
  - apiGroups: ["apps"]
    resources: ["deployments/scale", "deployments/status"]
    verbs: ["get", "patch"]
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "create", "delete"]
  - apiGroups: [""]
    resources: ["configmaps", "services"]
    verbs: ["get", "create"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ .Release.Name }}-intelliops-action
  labels:
    {{- include "intelliops.labels" . | nindent 4 }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ .Release.Name }}-intelliops-action
subjects:
  - kind: ServiceAccount
    name: intelliops-action
    namespace: {{ .Release.Namespace }}
{{- end }}
```
(Verify the verb list against Task-1's grep of the action adapters' actual API calls — the spec's table is the reference; adjust if the code needs more.)

- [ ] **Step 2: Set `serviceAccountName` on the action deployment**

In `service-deployment.yaml`, within the pod spec, add (only for the action service, only when rbac.create):
```yaml
{{- if and $.Values.rbac.create (eq .name "action") }}
serviceAccountName: intelliops-action
{{- end }}
```

- [ ] **Step 3: Validate**

Run: `helm template t deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml | grep -E "kind: (ClusterRole|ServiceAccount|ClusterRoleBinding)|serviceAccountName"`
Expected: the SA + ClusterRole + binding render, and the action Deployment (only) sets serviceAccountName. `helm template t deploy/k8s/platform` (default, rbac.create=false) shows NONE of these.
Also `kubectl --dry-run=client -f <(helm template ...)` if kubectl is available (validates the RBAC schema).

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/platform/templates/rbac.yaml deploy/k8s/platform/templates/service-deployment.yaml
git commit -m "feat(helm): scoped RBAC for the action service's k8s remediation + sandbox

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: LLM Secret + rca wiring

**Files:**
- Create: `deploy/k8s/platform/templates/llm-secret.yaml`.
- Modify: `deploy/k8s/platform/templates/service-deployment.yaml` (surface the key on rca via secretKeyRef).

**Interfaces:**
- Produces: the LLM API key reaches the **rca** pod as `INTELLIOPS_LLM_EXPLANATION_API_KEY` via `secretKeyRef`, from either a chart-created Secret (when `llm.apiKey` is set via `--set`) or a pre-existing Secret named by `llm.apiKeySecretName`. NEVER in git.

- [ ] **Step 1: Write `llm-secret.yaml`**

```yaml
{{- if .Values.llm.apiKey }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Release.Name }}-llm
  labels:
    {{- include "intelliops.labels" . | nindent 4 }}
type: Opaque
stringData:
  api-key: {{ .Values.llm.apiKey | quote }}
{{- end }}
```
Add `llm.apiKey: ""` to values.yaml (empty; set ONLY via `--set-string llm.apiKey=...` at install, never written to a values file in git). The Secret renders only when a key is supplied.

- [ ] **Step 2: Surface the key on rca**

In `service-deployment.yaml`, for the rca service only, add an env entry from the secret (chart-created OR external):
```yaml
{{- if eq .name "rca" }}
{{- $secretName := $.Values.llm.apiKeySecretName | default (printf "%s-llm" $.Release.Name) }}
{{- if or $.Values.llm.apiKey $.Values.llm.apiKeySecretName }}
            - name: INTELLIOPS_LLM_EXPLANATION_API_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ $secretName }}
                  key: api-key
{{- end }}
{{- end }}
```
(This goes in the explicit `env:` list the template already builds, alongside SERVICE_MODULE/PORT.)

- [ ] **Step 3: Validate secret hygiene**

Run: `helm template t deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml --set-string llm.apiKey=DUMMY_KEY_123 | grep -nE "DUMMY_KEY_123|secretKeyRef|kind: Secret"`
Expected: `DUMMY_KEY_123` appears ONLY inside `kind: Secret` (stringData) — NOT on the rca pod env (which shows `secretKeyRef`), NOT in the configmap. Then `helm template t deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml` (no --set key) → the rca env still references the secret name (for the external-Secret path) or omits it; no Secret rendered. Confirm NO key value is anywhere in the committed files: `git grep -i "api.key\|gsk_\|sk-" deploy/ | grep -v secretKeyRef | grep -v apiKey` → empty.

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/platform/templates/llm-secret.yaml deploy/k8s/platform/values.yaml deploy/k8s/platform/templates/service-deployment.yaml
git commit -m "feat(helm): LLM API key via Secret + rca secretKeyRef (never committed)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: demo-app + Meridian + Prometheus folded into the chart

**Files:**
- Create: `deploy/k8s/platform/templates/demo-app.yaml`, `deploy/k8s/platform/templates/meridian.yaml`, `deploy/k8s/platform/templates/prometheus.yaml` (Deployment+Service each; Prometheus also a scrape-config ConfigMap).
- Modify: `deploy/k8s/platform/values.yaml` (image refs for demo-app/meridian; enable flags), and reference the existing `deploy/k8s/{demo-app,meridian,prometheus}/*.yaml` as the source of truth for the workload shapes.

**Interfaces:**
- Produces: chart-managed demo-app, the 4 Meridian services, and Prometheus, with Prometheus scraping the in-cluster Services by DNS (demo-app, meridian-*, and the platform services' metrics endpoints if they expose any).

- [ ] **Step 1: Port the raw manifests into templates**

Read `deploy/k8s/demo-app/{deployment,service}.yaml` and the Meridian manifests `deploy/k8s/meridian/*.yaml`; render them as chart templates using the chart's label helpers, image from values (demo-app → base image with the demo module; Meridian → the `Dockerfile.meridian` image, a new `values.meridian.image`). Gate each behind a values flag (`demoApp.enabled`, `meridian.enabled`, `prometheus.enabled`, default true in values-live, sensible in defaults).

- [ ] **Step 2: Prometheus scrape config as a chart ConfigMap**

Create a Prometheus ConfigMap whose `prometheus.yml` scrapes the in-cluster Services by DNS:
```yaml
scrape_configs:
  - job_name: demo-app
    static_configs: [{ targets: ["demo-app:8000"] }]
  - job_name: meridian
    static_configs: [{ targets: ["meridian-gateway:8000","meridian-validation:8000","meridian-aggregation:8000","meridian-reporting:8000"] }]
```
(Use the actual metric ports the services expose — verify from the existing prometheus configmap + the services' /metrics endpoints. The point: real in-cluster series so per-metric health verification + the sandbox's future per-clone scraping see real data.)

- [ ] **Step 3: Validate**

Run: `helm lint deploy/k8s/platform && helm template t deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml | grep -E "name: (demo-app|meridian-gateway|prometheus)|job_name"`
Expected: the workloads + the Prometheus scrape jobs render. `kubectl --dry-run=client` if available.

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/platform/templates/demo-app.yaml deploy/k8s/platform/templates/meridian.yaml deploy/k8s/platform/templates/prometheus.yaml deploy/k8s/platform/values.yaml
git commit -m "feat(helm): fold demo-app, Meridian, and in-cluster Prometheus into the chart

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Frontend image + k8s serving with same-origin read proxy

**Files:**
- Create: `deploy/Dockerfile.frontend`, `deploy/nginx.conf` (or inline in the Dockerfile), `deploy/k8s/platform/templates/console.yaml` (Deployment+Service+NodePort).
- Modify: `deploy/k8s/platform/values.yaml` (console image + enable flag + NodePorts).

**Interfaces:**
- Produces: an nginx image serving the built console at `/` and reverse-proxying the read API at `/api/` (SSE-safe), so the browser reaches real data same-origin. The frontend builds with `VITE_DATA_MODE=live` and `VITE_READ_URL=/api`.

- [ ] **Step 1: Verify the frontend's backend URL surface**

Read `frontend/src/data/api.ts` — list every backend it calls (read, governance, correlation, rca URLs via `VITE_*`). The nginx proxy must cover each URL the LIVE views use. Note them for the proxy config. (If only the read service is needed for the arc views, proxy just `/api` → read; if governance/rca are used for Settings/Governance, proxy those too under distinct prefixes and set the corresponding `VITE_*` to the proxied paths.)

- [ ] **Step 2: `deploy/nginx.conf`**

```nginx
server {
  listen 8080;
  root /usr/share/nginx/html;
  index index.html;
  location / { try_files $uri $uri/ /index.html; }
  location /api/ {
    proxy_pass http://read:8000/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;          # REQUIRED for /stream SSE
    proxy_read_timeout 3600s;
  }
  # add /gov/, /rca/ blocks if api.ts needs them, each proxy_pass to that Service
}
```

- [ ] **Step 3: `deploy/Dockerfile.frontend`**

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_DATA_MODE=live
ENV VITE_READ_URL=/api
RUN npm run build

FROM nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
```
(Adjust `VITE_*` per Step 1. If the app reads other `VITE_*` backends, set them to the proxied prefixes.)

- [ ] **Step 4: `console.yaml` (Deployment + Service + NodePort)**

Render a console Deployment (the frontend image), a Service, and expose it + the read service via NodePort (e.g. console 30080, read 30007) so the browser reaches them on kind. Gate on `console.enabled`.

- [ ] **Step 5: Validate**

Run: `helm template t deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml | grep -E "name: console|nodePort|8080"` and, if Docker present, `docker build -f deploy/Dockerfile.frontend -t intelliops-console:test .` (the frontend build must succeed — it's the same `npm run build` that passes today). If Docker absent, validate the Dockerfile + nginx.conf by inspection and note build-unverified-in-session.

- [ ] **Step 6: Commit**

```bash
git add deploy/Dockerfile.frontend deploy/nginx.conf deploy/k8s/platform/templates/console.yaml deploy/k8s/platform/values.yaml
git commit -m "feat(helm): console image + nginx same-origin read proxy (SSE-safe) for live UI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: One-command bring-up script + README + ADR-030

**Files:**
- Create: `scripts/kind-up-full.sh`.
- Modify: `deploy/k8s/README.md`, `architectural.md` (ADR-030), `README.md` (ADR count 29→30), `docs/OPERATIONS.md`.
- Commit the spec + this plan.

**Interfaces:**
- Produces: a documented, single-sequence bring-up of the full stack on kind, and the docs that explain it.

- [ ] **Step 1: `scripts/kind-up-full.sh`**

A script (modeled on the existing `scripts/kind-up.sh`) that:
1. creates the kind cluster (reuse kind-config.yaml, add NodePort maps for console 30080 + read 30007),
2. builds all images (base, full, meridian, demo-app, console) and `kind load`s them,
3. `helm install intelliops deploy/k8s/platform -f deploy/k8s/platform/values-live.yaml --set-string llm.apiKey="$GROQ_API_KEY"` (reads the key from the operator's env — the script NEVER hardcodes it; if `$GROQ_API_KEY` is unset it warns and installs with LLM template),
4. waits for rollouts,
5. prints the console URL (http://localhost:30080) + read URL.
Make it idempotent + `set -euo pipefail`. Guard each external tool (docker/kind/kubectl/helm) with a presence check + friendly message.

- [ ] **Step 2: Update `deploy/k8s/README.md`**

Rewrite to document the full-stack path: prerequisites, `GROQ_API_KEY=... ./scripts/kind-up-full.sh`, what comes up, the console URL, how the live AI features are enabled (values-live), how remediation is now real, and the safe-default note (plain `helm install` = dry-run/off). Keep the existing probes/kubeconfig sections that still apply (or note they're superseded by in-cluster auth for the in-k8s path).

- [ ] **Step 3: ADR-030 + README count + OPERATIONS**

`architectural.md`: `### ADR-030 — Full in-cluster deployment (Helm)` after ADR-029, before `## 4. Cross-cutting concerns`. Cover: per-service images (rca/action full, CPU-torch, baked model), scoped RBAC for remediation + sandbox (why cluster-scoped: namespace create/delete), in-cluster auth selection, LLM key via Secret, the values-live opt-in posture (safe by default), the console same-origin proxy (SSE), and Prometheus scraping in-cluster. Cross-ref ADR-022 (slim images), ADR-023 (sandbox), ADR-007 (dry-run default). README "twenty-nine" → "thirty" (BOTH locations). OPERATIONS: the env matrix (safe vs live values).

- [ ] **Step 4: Final validation + commit**

Run: `helm lint deploy/k8s/platform` (clean) and `bash -n scripts/kind-up-full.sh` (script parses). If a cluster is available, a real `./scripts/kind-up-full.sh` end-to-end; otherwise document it as operator-run.
```bash
git add scripts/kind-up-full.sh deploy/k8s/README.md architectural.md README.md docs/OPERATIONS.md docs/superpowers/specs/2026-09-06-live-k8s-stack-design.md docs/superpowers/plans/2026-09-06-live-k8s-stack.md
git commit -m "docs(k8s): full-stack bring-up script + ADR-030; spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** AC1 (helm lint/template) → validation steps in T3-T7 + T8; AC2 (per-service images) → T3; AC3 (AI env) → T3; AC4 (RBAC) → T4; AC5 (in-cluster auth) → T1; AC6 (secret hygiene) → T5; AC7 (Prometheus in-cluster) → T6; AC8 (frontend live) → T7; AC9 (e2e runbook) → T8; AC10 (docs) → T8.
- **Only T1 touches the app/test suite** (the in-cluster auth code change, +2 unit tests). T2-T8 are Docker/Helm/nginx/scripts/docs — validated by `helm lint`/`helm template`/`docker build`/`bash -n`, NOT the pytest suite. So the full pytest suite stays at its current count +2 (T1's tests) and is otherwise untouched.
- **Safe-by-default proven:** every values addition defaults to the safe posture; `helm template` with default values (no overlay) must show dry_run/off/always/no-RBAC/no-Secret. The live posture is opt-in via `values-live.yaml`. This is asserted in T3/T4/T5 validation steps.
- **Secret hygiene proven:** T5 asserts a dummy key appears only inside `kind: Secret`, never on a pod env or the configmap, and `git grep` finds no key value in committed files.
- **In-session honesty:** T2/T7 image builds + T8 e2e are gated on Docker/kind being available; if not, the deliverable is validated configs + runbook, explicitly labeled unverified-in-session, never claimed as a live run.
- **Type/consistency:** the values fields introduced in T3 (`image.fullTag`/`fullRepository`, per-service `image`, `env.*`, `rbac.create`, `llm.apiKey`/`apiKeySecretName`) are consumed consistently in T3-T7 templates. The action verb table (T4) matches Task-1's grep of the adapters.
- **Known soft spot (flag for executor):** the frontend proxy (T7) depends on exactly which backends `api.ts` calls in live mode — T7 Step 1 verifies this before writing the proxy; if the app needs governance/rca directly (not just read), the proxy + `VITE_*` must cover them. Get this right or the live Settings/Governance views break (the Incidents/Overview arc views need only read + its /stream).
- **Ordering rationale:** code change first (T1, unblocks the k8s-mode path + is the only test-suite touch), then the image it deploys (T2), then the chart layers (T3 images/env → T4 RBAC → T5 secret → T6 workloads → T7 frontend), then the script+docs that tie it together (T8). Each task is independently `helm template`-validatable.
