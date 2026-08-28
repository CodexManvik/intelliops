# IntelliOps — TODO / Deferred Work

Living backlog of features and fixes deliberately deferred. Each entry: what, why deferred, and enough context to pick it up cold.

---

## HIGH — Real remediation against Meridian (deploy Meridian into k8s)

**What:** Today there are two disjoint demos: (1) **Meridian** on docker-compose — best detection/diagnosis story, but remediation is `dry_run` (logs steps, simulated healthy, never touches infra); (2) the **kind cluster** (`deploy/k8s/`) — real pod remediation via the Kubernetes API, but only against a single `demo-app`, not Meridian. The user wants **real remediation on Meridian** ("real performance, not simulated healthy").

**The gap:** the k8s remediator (`services/action/adapters/k8s_remediator.py`) drives Kubernetes deployments (scale/restart/rollback). Meridian is compose-only, so it has no k8s deployment to act on. To get real remediation *on Meridian*, Meridian must be **deployed into the kind cluster** with k8s manifests (Deployment + Service per meridian service), Prometheus scraping the in-cluster Meridian, and the action service in `k8s` mode targeting the meridian namespace.

**Work required:**
- k8s manifests for the 4 meridian services (mirror `deploy/k8s/demo-app/`): Deployment, Service, liveness `/health` + readiness `/ready` probes, resource requests so `scale`/`restart` are meaningful.
- A meridian namespace + Prometheus scrape config for in-cluster meridian (mirror `deploy/prometheus.yml` meridian jobs into the k8s Prometheus).
- A fault-injection path that works in-cluster: the Operations panel currently POSTs to the gateway ops proxy → `/admin/fault`; confirm that reaches the in-cluster pods (it should, via the gateway Service).
- **Key design question:** for a fault to be *healed by a restart*, the fault must live in the pod's process (like demo-app's in-memory `broken` flag) so `rollout restart` clears it. Meridian's `MeridianState` (`services/meridian/common.py`) IS in-process — so `restart-pod` should clear a meridian fault. Verify: does restarting a meridian pod reset `cpu`/`error_rate` to baseline? (It should — state is per-process.) `scale-service` won't clear it (same caveat as demo-app, see `deploy/k8s/README.md` §4).
- Update `deploy/k8s/README.md` (or a new meridian-k8s doc) with the meridian-on-kind flow.
- kind resource sizing: 4 meridian + demo-app + prometheus in one kind node — check it fits.

**Why deferred:** meaningful build (manifests + wiring + verification), needs kind + a clean design pass; not a demo-eve tweak. The existing `demo-app` k8s path (`deploy/k8s/README.md`) IS real and runnable today for a "real remediation" story if needed before this lands.

**Prior art:** `deploy/k8s/README.md`, `deploy/docker-compose.k8s.yml`, `scripts/kind-up.sh`/`kind-down.sh`, `deploy/k8s/demo-app/`.

---

## MEDIUM — Pre-flight / sandbox validation before remediation

**What (user's "sandbox" idea):** before executing a fix on the real target, run a **pre-flight validation step** (a dry trial / canary / policy check) and **show it in the UI** — so the flow becomes: diagnose → **pre-flight check passes** → approve → execute → verify → rollback. Today there is NO sandbox: it's execute → verify health → rollback-if-unhealthy (`services/action/remediate.py`), with `dry_run` mode meaning "log only" (not a real trial).

**Why it matters:** the user (correctly) expected a "try it safely first, confirm, then present" model. Adding a genuine pre-flight step would make the safety story stronger and match that mental model.

**Work required (rough):**
- Define what "pre-flight" means concretely: a schema/policy validation of the RemediationPlan? A canary (scale +1, observe, then commit)? A k8s `--dry-run=server` API call (real k8s admission check without applying)? The last is the cleanest "real sandbox" — Kubernetes' own server-side dry-run validates the change against the live cluster without mutating it.
- Add a `preflight()` step to `execute_remediation` (a new gate between HITL-approval and execute) that returns a pass/fail + details.
- Surface it in the incident drill-down UI (a "pre-flight" row in the timeline: validated ✓ before executed).
- Additive contract field for the preflight result; project it through read-model; render it.

**Why deferred:** it's a real feature (spec + build across action service + contracts + read projection + UI), not a quick change. User said "I want it but we will do it later."

---

## MEDIUM — Live Meridian metrics view in the console

**What:** The console has no screen showing Meridian's *scraped* metrics. `cpu_usage` + `meridian_error_rate` per meridian service are exposed at each service's `/metrics` and scraped by Prometheus every 5s (`deploy/prometheus.yml`), but the only ways to see them today are raw (`http://localhost:8008/metrics`, or Prometheus at `http://localhost:9090`) or indirectly (Settings → z-score baselines; the incident drill-down's "what broke" panel).

**Want:** a live panel (on the Settings/System page, or a small strip) that queries Prometheus (`GET /api/v1/query?query=cpu_usage` etc.) and shows the real per-service values updating — so a presenter can point at the spike *inside the console*.

**Work required:**
- A read-side or direct-from-browser Prometheus query (Prometheus HTTP API is `http://localhost:9090/api/v1/query`); add `VITE_PROM_URL` to the console env, or proxy through the read service to avoid CORS.
- A `<MeridianMetrics>` panel: poll the query, render per-service `cpu_usage`/`error_rate` with a healthy/broken indicator.
- Honest labeling (real Prometheus data, live).

**Why deferred (this pass):** was queued alongside the live-gates work; both are UI additions. Being built now if the user greenlit — otherwise here for later.

**Status:** REQUESTED for the current pass (see the live-gates item). If built, move to a merged PR and delete this entry.

---

## MEDIUM — Live Governance gate activity (not static cards)

**What:** The Governance page's three "gate" cards (`frontend/src/views/Governance.tsx`, the `gates` array) are **static descriptions**. The gates themselves ARE real and enforced in `services/action/remediate.py` (Gate 1 reversible-only, Gate 2 RBAC fail-closed, Gate 3 HITL), and the audit trail below the cards IS live proof they fire — but the cards don't *show* live activity.

**Want:** drive the cards from real audit data — e.g. a live count per gate of how many times it fired (`allow`/`deny`/`abort`), derived from the audit records (`GET /audit`), so they read as active enforcement, not documentation.

**Work required:**
- Aggregate audit records by decision/reason: `denied:rbac` → RBAC gate; `refused:not-reversible` → reversible gate; `aborted:*` → HITL gate; `allow` → passed.
- Add a live count/last-fired to each gate card, computed client-side from the already-loaded audit data.
- Keep the descriptive text; add the live numbers.

**Why deferred (this pass):** queued for the current pass alongside the metrics view.

**Status:** REQUESTED for the current pass. If built, move to a merged PR and delete this entry.

---

## LOW — Type tightening: AuditRow.ts / OutcomeRow.ts

**What:** `frontend/src/data/types.ts` types `ts: number` on `AuditRow`/`OutcomeRow`, but live mode delivers an ISO **string** (backend `datetime` → ISO). Handled safely at runtime (the ISO-aware `timeAgo` + a `new Date()`-based sort), so no bug — just an imprecise annotation. Tighten to `ts: number | string` to match `timeAgo`'s signature.

**Why deferred:** cosmetic; no runtime effect. Flagged in the console-streamline final review (PR #30).

---

## LOW — mock-mode drill-down fixtures

**What:** In `VITE_DATA_MODE=mock`, the incident drill-down panels (member events, z-score, evidence, explanation) render blank because the mock situations in `frontend/src/data/mock.ts` don't carry those fields. Correct per "no fabricated data," but not demo-visible in mock. Live mode is fully populated.

**Want (optional):** enrich the mock fixtures so a mock-mode demo also shows the drill-down.

**Why deferred:** live mode is the demo path; mock is a fallback. Flagged in the honesty-and-evidence effort (PR #27).

---

## LOW — `/system` LLM state can lag a live UI swap

**What:** The read service's `GET /system` reports the LLM provider from env `settings`, so after a live swap via `POST /config/llm` (Settings panel), the System-view *state-grid row* still shows the old provider until restart. The authoritative **badge** reads the live `/config/llm` and IS correct; only the secondary grid row is env-sourced. Documented as intentional in the honesty spec.

**Want (optional):** point `/system`'s llm block at the rca service's live `/config/llm` so the grid row matches the badge.

**Why deferred:** the badge is the source of truth and is correct; the grid row is a minor secondary display. Flagged in PR #27 final review.
