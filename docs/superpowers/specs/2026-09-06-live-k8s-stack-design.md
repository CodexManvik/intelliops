# Live Kubernetes Stack — Design

**Status:** approved decisions (2026-09-06); spec for review.
**Goal:** Deploy the ENTIRE IntelliOps platform to Kubernetes via the Helm chart — all 7 services + Meridian + demo-app + Postgres + Redis + Prometheus + the React console — with the metrics-arc AI features genuinely live (embedding runbook selection + LLM explanations), real k8s remediation, and per-metric health verification against real Prometheus. "Nothing mocked": the console runs in `live` mode against the in-cluster read service.

## Context — what exists vs. what's missing

The repo already has a Helm chart (`deploy/k8s/platform/`) that renders the 7 services (Deployment+Service each), a Postgres, a Redis, a pre-install `alembic` migrate Job, and a `configmap.yaml` with basic env. Separately, `deploy/k8s/` has raw manifests for **demo-app** and **Prometheus** (+ a `kind-config.yaml`), and `scripts/kind-up.sh` stands up a kind cluster with just those two. The documented "real remediation" path today runs the 7 services in **docker-compose** (with `docker-compose.k8s.yml` flipping the action service to k8s mode via a mounted kubeconfig) while only demo-app + Prometheus live in kind.

**Gaps for a full-in-k8s, AI-live deployment (this spec closes them):**

1. **Single image, wrong build target for AI.** The Dockerfile has `base` (no extras) and `full` (base + `ml` + `k8s`). The chart uses one `image.tag` for every service. Phase 3's embedding selector lives in **rca** and needs `sentence_transformers` (the `ml` extra); Phase 4's k8s health + the action remediator/sandbox need the `k8s` extra. So `rca` and `action` MUST run the `full` image, or embedding + real remediation ImportError at runtime. The chart has no per-service image selection.
2. **No AI / mode env.** The configmap sets only AUTH/STORE/BUS/REDIS/DATABASE. Missing: `RUNBOOK_SELECTOR_MODE`, `CORRELATOR_KIND`, `DETECTION_POLICY`, `HEALTH_CHECK_MODE`, `REMEDIATOR_MODE`, `SANDBOX_MODE`, `PROMETHEUS_URL`, and the LLM `LLM_EXPLANATION_*`. So embedding is off, LLM template, health `always`, remediation dry-run — none of the arc's live features are on.
3. **No RBAC.** The action service calls the k8s API (see the verb inventory below) — including cluster-scoped namespace create/delete for the sandbox. In-cluster it authenticates as its pod ServiceAccount, which by default can do nothing. Needs a ServiceAccount + ClusterRole + ClusterRoleBinding.
4. **No in-cluster auth wiring.** Today the action service loads a mounted kubeconfig (`config.load_kube_config()`). In-cluster it must use `config.load_incluster_config()`. The adapter must pick the right loader; the deployment must set the ServiceAccount.
5. **LLM key handling.** The API key is a secret — must be a k8s Secret surfaced as an env var on rca, never the configmap.
6. **demo-app + Meridian + Prometheus not in the chart.** They're raw manifests (demo-app, prometheus) or only in compose (Meridian). For "everything in k8s" they must deploy as part of (or alongside) the release, and Prometheus must scrape the in-cluster targets (its current config uses static DNS at compose names).
7. **Prometheus scrape config is compose-oriented.** `deploy/k8s/prometheus/configmap.yaml` (per the Phase-4 finding) uses `static_configs` at fixed names. In-cluster it must scrape the k8s Services (demo-app, meridian, the platform services) by their in-cluster DNS.
8. **No frontend deployment.** The React console isn't in k8s at all. For a live demo it needs a build + a static-serve Deployment/Service (nginx) + a way to reach the read service, with `VITE_DATA_MODE=live` and `VITE_READ_URL` pointing at the in-cluster read service (via its browser-reachable URL).

## Action service — exact k8s API surface (drives RBAC)

From `services/action/adapters/{k8s_remediator,sandbox,k8s_health}.py`:

| API group / resource | Verbs needed | Why |
|---|---|---|
| `apps/v1` deployments | get, list, patch, create | scale/restart/rollback + sandbox clone |
| `apps/v1` deployments/scale | patch | horizontal scale |
| `apps/v1` deployments/status | get | health: readyReplicas |
| `apps/v1` replicasets | list, create | rollout history / clone |
| `core/v1` namespaces | create, delete, get | sandbox throwaway namespace (**cluster-scoped**) |
| `core/v1` configmaps | create, get | sandbox clone of config |
| `core/v1` services | create, get | sandbox clone of the Service |
| `core/v1` pods | list, get | (health / diagnostics — include read verbs) |

Because namespaces are cluster-scoped, the binding is a **ClusterRole + ClusterRoleBinding** (a namespaced Role cannot grant namespace create/delete). This is inherent to the sandbox pre-flight design (ADR-023) — it clones into a throwaway namespace. We scope the ClusterRole tightly to exactly these groups/resources/verbs.

## Design

### 1. Per-service image target (`base` vs `full`)
Extend `values.yaml` `services[]` with an optional `image` override per service (e.g. `full`), and the deployment template selects the image accordingly. Two workable shapes — the plan picks one:
- **(a)** Two published image repos/tags: `image.repository:image.tag` (base) and `image.fullRepository:image.tag` (full). Each service entry gets `image: base|full`; the template maps it.
- **(b)** A single `image.tag` but a per-service `imageSuffix`/repo. 
Decision (plan pins): give `rca` and `action` the **full** image; the other five use base. Document the rca image-size trade-off (torch/sentence-transformers). The migrate Job uses base.

### 2. AI + mode env (configmap + values)
Add to `values.yaml` `env:` and thread through the configmap (all `INTELLIOPS_`-prefixed):
- `CORRELATOR_KIND: robust` (seasonal per-(metric,hour) baselines — best for the metric surface),
- `DETECTION_POLICY: "on"` (Phase 2 — so detect and verify agree),
- `RUNBOOK_SELECTOR_MODE: embedding` (Phase 3 — real symptom-fit confidence),
- `REMEDIATOR_MODE: k8s`, `SANDBOX_MODE: k8s`, `HEALTH_CHECK_MODE: k8s` (Phase 4 + real remediation),
- `PROMETHEUS_URL: http://prometheus:9090` (in-cluster Service DNS),
- `LLM_EXPLANATION_ENDPOINT` + `LLM_EXPLANATION_MODEL` (values; endpoint e.g. Groq `https://api.groq.com/openai/v1`, model e.g. `llama-3.3-70b-versatile`),
- `LLM_EXPLANATION_API_KEY` — **from a Secret**, not the configmap.
These are values-driven so a `values-live.yaml` (or `--set`) enables the live posture while the chart's defaults stay safe (dry-run/off) for anyone who just `helm install`s without opting in. **Default values keep today's safe posture; a `values-live.yaml` overlay turns the live features on.**

### 3. LLM Secret
A `secret.yaml` template (created only when `env.LLM_EXPLANATION_API_KEY` is set, or referencing an existing Secret name) surfaced on the **rca** deployment as `INTELLIOPS_LLM_EXPLANATION_API_KEY` via `secretKeyRef`. The key is NEVER committed — supplied at install time via `--set` (from the user's shell) or `kubectl create secret` referenced by name. The spec/plan/docs show the command; Claude never handles the key value.

### 4. RBAC (ServiceAccount + ClusterRole + ClusterRoleBinding)
New `rbac.yaml` template:
- A ServiceAccount (e.g. `intelliops-action`).
- A ClusterRole granting exactly the verbs in the table above.
- A ClusterRoleBinding to the SA.
The **action** deployment sets `serviceAccountName: intelliops-action`. Other services keep the default SA (they make no k8s API calls). Gate RBAC creation on a `values` flag (`rbac.create: true`) so a dry-run-only install can skip it.

### 5. In-cluster auth
`services/action/adapters/*` currently call `config.load_kube_config()`. Change the client factory to try `load_incluster_config()` first (when running in a pod — detected via the standard `KUBERNETES_SERVICE_HOST` env or a try/except), falling back to `load_kube_config()` for the compose/local path. This is the ONE code change (small, in the k8s client bootstrap of the action adapters); everything else is chart/manifests. It must not break the existing compose-with-kubeconfig path or any test (the adapters are only exercised in k8s mode, guarded).

### 6. demo-app, Meridian, Prometheus in the release
Fold the demo-app + Meridian (4 services) + Prometheus into the Helm chart (new templates, or values-driven entries), OR keep them as raw manifests applied by the install script — the plan chooses the lower-risk path. Prometheus's scrape config becomes a chart-managed ConfigMap that scrapes the in-cluster Services by DNS (demo-app, meridian-*, and optionally the platform services' own metrics). This is what makes per-metric health verification and the sandbox's future per-clone scraping actually see real series.
- Meridian images use `Dockerfile.meridian`; the demo-app uses the shared Dockerfile. The plan wires their image refs.

### 7. Frontend (React console) in k8s
A multi-stage build (Node build → nginx static serve). **The browser-reachability problem is the crux and is settled here, not left to the plan:** the console's JS runs in the operator's browser ON THE HOST, so it cannot use in-cluster DNS (`http://read:8000`) — it must call the read service at a **host-reachable URL**. Design:
- **Expose `read` on a stable host port** via NodePort (kind maps it, like Prometheus's 30090) — e.g. read at NodePort `30007`. A real cluster would use an Ingress; kind uses NodePort (documented).
- **Serve the console via nginx that reverse-proxies the read API**, so the browser only ever talks to ONE origin (the console's) and same-origin avoids CORS + keeps SSE simple. nginx config: static files for `/`, and `location /api/ { proxy_pass http://read:8000/; proxy_buffering off; proxy_set_header Connection ''; proxy_http_version 1.1; }` — `proxy_buffering off` + HTTP/1.1 are REQUIRED for the `/stream` SSE to work through nginx (otherwise events buffer and the live console stalls). The frontend then uses `VITE_READ_URL=/api` (same-origin, proxied) — no host-DNS baked in, portable across kind/real clusters.
- A `Dockerfile.frontend` builds `frontend/` with `VITE_DATA_MODE=live` and `VITE_READ_URL=/api`; the nginx layer carries the proxy config. Console exposed via NodePort (e.g. `30080`).
- The read service's other consumers (governance/corr/rca URLs the console uses, per `frontend/src/data/api.ts`) are proxied the same way, or the api client is confirmed to only need the read service for the live views. The plan verifies `api.ts`'s full URL surface and proxies each needed backend under `/api/...`.
This is the piece that makes the metrics-arc UI (the paired frontend change) show REAL data — via a same-origin nginx proxy with SSE-safe buffering, the robust portable design.

### 8. Install flow + scripts
Extend/refactor the kind path so a single documented sequence brings up EVERYTHING:
- build the images (base, full, meridian, demo-app, frontend) and load into kind,
- `helm install` with `values-live.yaml` (+ the LLM secret),
- the migrate Job runs pre-install,
- Prometheus scrapes in-cluster targets,
- print the console URL + read URL.
A `scripts/kind-up-full.sh` (or extended `kind-up.sh`) automates it; the k8s README documents the manual steps too.

## Acceptance criteria

1. **`helm template` / `helm lint` clean** with both default values (safe posture) and `values-live.yaml` (live posture). Rendered manifests are valid k8s.
2. **Per-service images:** rendered rca + action Deployments reference the `full` image; the other five + migrate reference base. Verifiable in `helm template` output.
3. **AI env present under live values:** rca gets `RUNBOOK_SELECTOR_MODE=embedding` + the LLM endpoint/model + the API key via secretKeyRef; correlation gets `CORRELATOR_KIND` + `DETECTION_POLICY=on`; action gets `REMEDIATOR_MODE/SANDBOX_MODE/HEALTH_CHECK_MODE=k8s` + `PROMETHEUS_URL`. Default values keep the safe posture (embedding off, LLM template, dry-run).
4. **RBAC:** with `rbac.create=true`, a ServiceAccount + ClusterRole (exactly the verb table) + ClusterRoleBinding render, and the action Deployment sets `serviceAccountName`. `helm template` shows no other SA.
5. **In-cluster auth:** the action k8s client uses `load_incluster_config()` in a pod and `load_kube_config()` otherwise; existing action tests still pass; the compose k8s path is unaffected. Unit-tested (the loader selection is mockable).
6. **Secret hygiene:** the LLM key is never in the chart/configmap/git; it enters via `--set`/existing-Secret at install. `helm template` with a dummy `--set` shows it only in the Secret + a `secretKeyRef` (not inline on the pod, not in the configmap).
7. **Prometheus scrapes in-cluster:** the chart-managed Prometheus config targets the in-cluster Services (demo-app, meridian, …) by DNS; a live bring-up shows real series for the metric families.
8. **Frontend live:** the console image builds; deployed, it loads in `live` mode and reaches the read service (REST + SSE) so the metrics-arc UI shows real data (not mock).
9. **End-to-end (documented +, where feasible, run):** a single runbook (script + README) brings up the full stack on kind; a fault injected via Meridian/demo-app flows detect → diagnose (real embedding confidence, real LLM explanation) → approve → real pod remediation → per-metric health verification → outcome, visible in the live console. Where a full live run isn't possible in-session, every config is validated (`helm template`, image builds) and the runbook is precise; nothing is faked.
10. **Docs:** `deploy/k8s/README.md` updated to the full-stack flow; a new ADR (ADR-030) records the in-k8s deployment (per-service images, RBAC for remediation, in-cluster auth, secret handling, live AI posture via values overlay); OPERATIONS notes the env matrix. README ADR count 29→30.

## Constraints & risks

- **Image size + CPU-only torch (decided):** the `full` image pulls sentence-transformers, whose transitive backend is PyTorch. **Torch is used for INFERENCE ONLY** — `EmbeddingRunbookSelector` calls `model.encode(text)` for cosine fit; there is NO training/fine-tuning/CUDA anywhere (the one `.fit()` in the tree is river/sklearn in the unrelated `trained` correlator, not used under `CORRELATOR_KIND=robust`). So the full image pins the **CPU-only torch wheel** (`--extra-index-url https://download.pytorch.org/whl/cpu`), cutting the image from ~6GB (CUDA) to roughly ~1GB, with ample inference speed on short symptom strings. rca+action use the full image; the plan wires the CPU-torch pin.
- **Model baked (decided):** all-MiniLM-L6-v2 (~90MB) is pre-downloaded into the full image at build time (a build step runs `SentenceTransformer("all-MiniLM-L6-v2")` so the weights are cached in the image), so rca works air-gapped with no first-request latency spike or HuggingFace egress dependency.
- **Workloads folded into Helm (decided):** demo-app + Meridian (4) + Prometheus become chart-managed templates, so a single `helm install` brings up everything.
- **Slim-boundary unaffected:** this is deploy/infra; the slim-boundary guard (services import without heavy deps) is a code property already tested and untouched. The base image staying ml-free preserves it.
- **Safety defaults preserved:** the chart's DEFAULT values keep dry-run/off/template — the live posture is strictly opt-in via `values-live.yaml`. So `helm install` without the overlay is safe; nothing in a real cluster is touched unless the operator opts in (mirrors the existing REMEDIATOR_MODE default-dry-run principle).
- **The LLM key is the user's** — never handled by Claude, never committed; supplied at install.
- **In-session verification honesty (important):** a full live bring-up on kind needs Docker + kind + kubectl + Helm in THIS environment and pulls multi-GB images (torch). That may not be runnable here. The GUARANTEED deliverable is: every chart/manifest validated with `helm lint` + `helm template` (rendered manifests asserted correct — images, env, RBAC, secret refs, proxy config), the one code change unit-tested, and a precise, tested-as-far-as-possible runbook. A live end-to-end run happens only if the environment supports it; otherwise the spec is explicit that the operator runs the documented, validated sequence. Nothing is claimed as "verified live" unless it actually ran — `helm template` assertions are labeled as such, not as a live run.
- **Scope:** this is deployment + one small in-cluster-auth code change. It does NOT change detection/RCA/action/verification logic (the metrics arc is done). It does NOT alter the frontend app code (the paired frontend change owns that) — it only adds the frontend's k8s packaging.

## Out of scope
- Cloud-provider specifics (EKS/GKE/AKS) beyond generic k8s + a kind path. Ingress uses NodePort for the kind demo; a real Ingress is noted but not required.
- Autoscaling/HPA, network policies, pod security policies (notable for prod; not needed for the demo).
- Multi-replica / HA of the platform services (single replica, as today).
- Secret management beyond a plain k8s Secret (no Vault/SOPS) — noted as a prod follow-up.
