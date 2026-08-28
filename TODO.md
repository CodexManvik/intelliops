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

## DONE — Live Meridian metrics view

**Status:** DONE / shipped in the `feat/live-ui-additions` PR. Landed in the **Meridian UI as a new Metrics page** (not the IntelliOps console) — a 3rd nav item, live per-service `cpu_usage`/`error_rate` polled from Prometheus via a new gateway proxy endpoint (`GET /api/ops/metrics`), with an honest empty state when Prometheus is unreachable (`{scraped:false, services:[]}`, fail-soft, never a 5xx).

**What it was:** The console had no screen showing Meridian's *scraped* metrics. `cpu_usage` + `meridian_error_rate` per meridian service are exposed at each service's `/metrics` and scraped by Prometheus every 5s (`deploy/prometheus.yml`), but the only ways to see them were raw (`http://localhost:8008/metrics`, or Prometheus at `http://localhost:9090`) or indirectly (Settings → z-score baselines; the incident drill-down's "what broke" panel).

---

## DONE — Live Governance gate activity (not static cards)

**Status:** DONE / shipped in the `feat/live-ui-additions` PR. The Governance page's three gate cards now show real passed/blocked counts + last-fired, computed client-side from the already-loaded audit/outcomes data — no fabricated numbers.

**What it was:** The Governance page's three "gate" cards (`frontend/src/views/Governance.tsx`, the `gates` array) were **static descriptions**. The gates themselves ARE real and enforced in `services/action/remediate.py` (Gate 1 reversible-only, Gate 2 RBAC fail-closed, Gate 3 HITL), and the audit trail below the cards was live proof they fire — but the cards didn't *show* live activity until this pass.

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

## LOW/MEDIUM — `useLiveData.ts` dev-only React StrictMode bug

**What:** Under `npm run dev` (StrictMode's double-invoke of effects), the `audit`/`outcomes` `useLiveData` hooks can get stuck at `loading:true` / empty data even though the underlying network calls return 200. Root cause is the interaction between StrictMode's mount→cleanup→remount effect cycle and the `let alive` closure-flag cleanup pattern in `frontend/src/hooks/useLiveData.ts` — the first mount's in-flight `tick()` promise can resolve after cleanup has already flipped `alive = false`, and depending on timing the remounted effect's own state updates can be missed by the component's render.

**Verified:** production builds (`vite build`) are **unaffected** — this only reproduces under `npm run dev` + StrictMode's double-invoke, not in the shipped bundle. Not a shipped-behavior defect; a dev-experience follow-up.

**Blast radius:** `useLiveData` is shared — used by `Governance.tsx`, `System.tsx`, and `Incidents.tsx`, so any of these can show the stuck-loading symptom in dev mode.

**Suggested fix:** replace the `let alive` closure-flag cleanup with an `AbortController`-based cleanup (abort on unmount/re-run, check `signal.aborted` instead of `alive` before each state update).

**Why deferred:** dev-only; found during the Task 3 (live Governance gate activity) build. Flagged in the `feat/live-ui-additions` final review.

---

## LOW — `/system` LLM state can lag a live UI swap

**What:** The read service's `GET /system` reports the LLM provider from env `settings`, so after a live swap via `POST /config/llm` (Settings panel), the System-view *state-grid row* still shows the old provider until restart. The authoritative **badge** reads the live `/config/llm` and IS correct; only the secondary grid row is env-sourced. Documented as intentional in the honesty spec.

**Want (optional):** point `/system`'s llm block at the rca service's live `/config/llm` so the grid row matches the badge.

**Why deferred:** the badge is the source of truth and is correct; the grid row is a minor secondary display. Flagged in PR #27 final review.
