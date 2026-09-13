# IntelliOps — TODO

Two parts: **[the active plan](#the-plan--a-fully-working-end-to-end-prototype)** (what we're building now, in order),
and the **[backlog](#backlog--deferred-work)** (deliberately deferred items, kept warm enough to pick up cold).

---

# The plan — a fully working end-to-end prototype

**Updated 2026-09-13.**

## The framing

The loop already runs end to end: detect → diagnose → gate → remediate on real Kubernetes → verify →
learn. That part is real and verified. What it has *not* faced is a problem it wasn't designed for.

Every fault we test with is one of Meridian's eight injectable types
([`services/meridian/common.py:21-27`](services/meridian/common.py)), and seven of them map 1:1 to a
rule we wrote in [`services/rca/rank.py`](services/rca/rank.py). We defined the problem to match the
solution. That's why the demo always looks good — and why it isn't yet a prototype you'd trust.

**So the work is not "replace Meridian."** Meridian is a good target. The work is to make the loop
survive the three things real incidents do that our tests never do:

1. **Be undiagnosable** — a cause no rule anticipated.
2. **Be lossy** — a crash mid-handler that drops the event.
3. **Be messy** — several faults at once, cascading across services.

Handling all three *gracefully* is a stronger story than eight-for-eight green, because it shows the
system knows the edge of what it knows. That is the difference between a demo and a product.

**Prerequisite for every verification step below:** Docker Desktop must be running
(`kind` v0.32.0 is installed; the daemon was down as of 2026-09-13).

---

## Definition of done

The prototype is "fully working end to end" when all of these are true **on one `kind` cluster, in one
run, without hand-holding**:

- [ ] A fault injected into Meridian is detected, diagnosed, gated, remediated on real pods, and
      verified healthy — with the console showing each stage live. *(works today)*
- [x] An **undiagnosable** fault is escalated to a human as an explicit "needs attention" state —
      not silently recorded as a failed remediation. *(P1 — done)*
- [ ] **Killing a service mid-incident loses nothing** — the event is redelivered and the loop
      completes, with the recovery documented in numbers. *(P2)*
- [ ] A **restarted console** rebuilds its full incident history instead of coming up blank. *(P2)*
- [ ] **Two concurrent faults on different services** produce two correctly-attributed incidents,
      not one merged blob. *(P3)*
- [x] Success-rate KPIs count only incidents the system actually attempted. *(P1 — done)*

---

## P1 — Teach the system to say "I don't know"

**The single highest-value change, and the smallest.** It converts our biggest conceptual weakness
into a deliberate design statement.

### P1.1 — Escalation is a first-class outcome  ·  ✅ DONE

**The bug.** When RCA can't find a cause it correctly emits a fallback hypothesis —
`"root cause undetermined from available signals"`, `suggested_runbook_id=None`, confidence 0.2
([`rank.py:141-150`](services/rca/rank.py)). But downstream, the action consumer turns that into
`RemediationResult.FAILURE` with `health_after="skipped:no-playbook"`
([`action/consumer.py:35-42`](services/action/consumer.py)), because the enum has no other option —
it is only `SUCCESS | FAILURE | ROLLED_BACK` ([`contracts.py:36-39`](common/contracts.py)).

**Why it matters.** An incident *nobody attempted to fix* is recorded as a **failed remediation**. That:
- corrupts the success-rate KPI (we look worse than we are, for the wrong reason),
- feeds a meaningless signal into the feedback/graduation loop, and
- gives the operator no signal that a human is actually needed.

**Work:**
- Add an escalation result to `RemediationResult` (e.g. `ESCALATED = "escalated"`) — additive, so
  existing consumers keep working.
- `action/consumer.py`: when `select_playbook` returns `None`, emit `ESCALATED` with a reason, not
  `FAILURE`.
- Exclude escalations from success-rate metrics and from the feedback learning path — they are not
  evidence about any runbook.
- Add the status mapping in [`read/projection.py:24-25`](services/read/projection.py) — today
  `SUCCESS → "resolved"` and `FAILURE → "failed"` are the only two, so an undiagnosable incident
  currently renders as a red **"failed"** card. Add `ESCALATED → "needs_attention"`.
- Render a **"Needs Attention"** state in the console (Incidents + Pipeline), visually distinct from
  both "resolved" and "failed".

**Acceptance:** inject a fault no rule matches → the console shows an incident parked in *Needs
Attention* with the undetermined-cause explanation; the KPI tile does not count it as a failure; the
audit trail records the escalation.

### P1.2 — Give `crash` a real diagnosis path  ·  ✅ DONE

`crash` is the one Meridian fault with **no dedicated RCA rule** — documented honestly at
[`architectural.md:1049`](architectural.md). It sets `unhealthy=True` and moves no metrics
([`meridian/common.py:27`](services/meridian/common.py)).

**ANSWERED (2026-09-13) — `crash` is injectable but NOT detected.** Verified directly against the code:
`MeridianState.apply()` sets `self.unhealthy = True` and nothing else; that flag is **read nowhere in
production** (only in tests); it is **not** among the 11 gauges `/metrics` exposes; and Meridian calls
`create_app(name)` with **no `readiness` callable**, so `/ready` returns 200 even "crashed" (it only
fails on a bus-ping failure). A crashed service emits byte-identical exposition to a healthy one — no
telemetry changes, no Situation is created, RCA is never reached.

Six doc locations claimed otherwise and have been corrected (`docs/MERIDIAN.md` ×3,
`architectural.md:1049`, and two `docs/superpowers/` files still to fix).

**Consequence:** P1.2's "route it to escalation" option is *not reachable* — an escalation needs a
Situation, and `crash` never produces one. Making it real is its own PR: expose a `service_up` gauge
on `/metrics`, add that series to **both** ingestion allowlists (`deploy/docker-compose.yml`,
`deploy/k8s/platform/values-live.yaml`), and add a detection rule — a constant-then-flip series scores
z=0 while sd==0, so the z-score correlator alone will not catch it.

**FIXED.** `crash` now drives a real scraped gauge, `service_up` (1 → 0), added to both ingestion
allowlists, with a dedicated `rank_hypotheses` rule at confidence 0.7 → `restart-pod` (a wedged
process is recycled, not scaled; new replicas of a wedged image are still wedged). Ranked below the
deploy rule (0.8), so a preceding deploy still wins as the better explanation.

### P1.3 — Make the escalation demonstrable  ·  ✅ DONE

**Found while building P1.1, and it is the sharpest evidence for this whole plan's framing.** Probing
`rank_hypotheses` with every metric Meridian actually exposes:

```
  cpu_usage                -> scale-service    (conf 0.6)
  meridian_error_rate      -> restart-pod      (conf 0.58)
  request_rate             -> scale-service    (conf 0.55)
  latency_p50_ms           -> scale-service    (conf 0.55)
  latency_p99_ms           -> scale-service    (conf 0.55)
  memory_usage_mb          -> restart-pod      (conf 0.65)
  saturation               -> scale-service    (conf 0.6)
  queue_depth              -> scale-service    (conf 0.55)
  db_pool_in_use           -> restart-pod      (conf 0.62)
  disk_usage_percent       -> scale-service    (conf 0.6)
  tls_handshake_failures   -> None             (conf 0.2)   <- only an UNMAPPED family escalates
```

**Every metric Meridian can emit maps to a runbook.** So the escalation state built in P1.1 is real,
tested and correct — and currently **impossible to trigger from any Meridian fault**. The demo target
was built so that every problem it can show already has an answer. That is the "we have a solution,
what's the problem?" pattern in one table.

**Work:** add a fault type that emits a metric family no RCA rule knows (e.g. `unknown_signal` →
`tls_handshake_failures`): a state field + gauge in `services/meridian/common.py`, the new series added
to **both** ingestion allowlists (`deploy/docker-compose.yml:66`,
`deploy/k8s/platform/values-live.yaml:31`), and a button in the Meridian ops panel.
`FaultSpec.type` is a plain `str` with no enum validation, so no contract change is needed.

**Feasibility checked:** a constant baseline scores z=0 while `sd == 0`
([`river_correlator.py:57`](services/correlation/adapters/river_correlator.py)), but the value moving
makes variance non-zero, so it detects on the next scrape — exactly how every existing fault behaves.

**DONE.** `unknown_signal` moves `tls_handshake_failures` (healthy 0.4 → broken 47.0), a family
outside every `rank_hypotheses` token. Both new series are in the compose and k8s ingestion
allowlists, and the fault is a preset in the Meridian Operations panel ("Unclassified anomaly").
Re-probing after the change:

```
  service_up               -> restart-pod      (conf 0.7)    <- crash is now diagnosable
  tls_handshake_failures   -> None             (conf 0.2)    <- escalates to a human
```

**Live acceptance (still to run):** fire "Unclassified anomaly" on Meridian reporting → an incident
appears, is diagnosed as "root cause undetermined", and parks in **Needs Attention** with nothing
executed. That is the last unverified step of P1's end-to-end story.

---

## P2 — Make the loop survive failure

This is the gap between "closed loop" as a claim and as a fact. Both items are already filed and
documented in [ADR-031](architectural.md).

### P2.1 — At-least-once delivery ([#53](https://github.com/CodexManvik/intelliops/issues/53))

Today `RedisBus.consume` acks **before** yielding to the handler, and the Kafka binding auto-commits
before processing — so a crash, validation error, or DB failure mid-handler **permanently loses** the
event. A dropped `remediation.outcomes` silently never closes the learning loop; a dropped
`situations.detected` drops an incident.

**Work:** move the `xack` to after the handler returns · stable event ids + a per-consumer idempotency
record so redelivery is safe · a per-topic dead-letter queue for poison messages · a transactional
outbox for handlers that both write Postgres and emit a follow-on event.

**Acceptance:** kill a consumer mid-handler; on restart the event is redelivered and processed exactly
once (no duplicate side effects). A poison message lands in the DLQ instead of wedging the consumer.

### P2.2 — Read model rebuilds on cold start ([#58](https://github.com/CodexManvik/intelliops/issues/58))

The console's projection is in-memory and rebuilt from the bus, but the consumer group resumes *past*
its prior acks — so a restarted read-service shows an empty console until new traffic arrives.

**Work:** snapshot/checkpoint the projection, or replay from `0` on cold start.

**Acceptance:** restart the read service mid-demo; the console comes back with full history.

---

## P3 — Make the loop handle mess

### P3.1 — Concurrent, cascading faults

`CorrelationEngine` groups anomalies by **time window, not by service**, so two faults on two services
inside the window merge into a single Situation. This is real and was confirmed live — which is why
Meridian's ops panel *disables* concurrent injection and shows a "~15s window" banner
([ADR-020](architectural.md)). We encoded the limitation into the UI rather than fixing it.

That was the right call for a scripted demo. It is the wrong shape for a prototype, because **real
incidents cascade** — one service's failure is another's dependency outage.

**Work:** add per-service (or per-dependency-group) grouping to `CorrelationEngine` behind a config
switch defaulting to today's behavior (the [ADR-012](architectural.md) pattern), then lift the
sequential-injection constraint in the Meridian ops panel.

**Acceptance:** fire faults on `gateway` and `reporting` simultaneously → two distinct Situations,
each attributed to the right service, each diagnosed independently.

### P3.2 — The chaos scenario, with numbers

The last open item from **Stream D** in [WORKPLAN.md](WORKPLAN.md). `scripts/chaos.sh` exists but
`docs/OPERATIONS.md` still has no documented recovery numbers.

**Work:** kill a service mid-incident, measure and document consumer-group recovery — time to
redelivery, events processed, zero loss (which only becomes *true* after P2.1).

**Acceptance:** a reproducible scenario in `docs/OPERATIONS.md` with real measured numbers.

---

## P4 — Production edges (tracked, deliberately deferred)

Documented as known limitations in [ADR-031 / ADR-032](architectural.md) and §6. Not in scope for the
prototype; listed so they are never mistaken for oversights.

| Issue | Item |
|---|---|
| [#59](https://github.com/CodexManvik/intelliops/issues/59) | RBAC trusts caller-supplied identity — anyone reaching Governance can approve *as* another actor |
| [#60](https://github.com/CodexManvik/intelliops/issues/60) | Browser auth embeds a shared token; SSE carries it in the URL |
| [#57](https://github.com/CodexManvik/intelliops/issues/57) | Workload hardening — resource limits, securityContext, NetworkPolicies (credentials half shipped in #62) |
| [#61](https://github.com/CodexManvik/intelliops/issues/61) | Split the `full` image so action/correlation don't inherit the ML stack |

---

## Suggested order

**P1 first** — smallest, highest narrative value, and it makes the KPIs honest before we measure
anything else. **Then P2.1**, because "nothing is lost" is the claim everything else rests on (and
P3.2's numbers are meaningless without it). **P2.2 and P3** follow. **P4 stays parked.**

---

# Backlog — deferred work

Each entry: what, why deferred, and enough context to pick it up cold.

---

## DONE — Real remediation against Meridian (deploy Meridian into k8s)

**Status:** DONE / shipped in `feat/meridian-k8s-remediation`. Approach: the 4
Meridian services are deployed into the kind cluster as `meridian-<svc>`
Deployments + NodePort Services, name-aligned to both the Prometheus scrape
`service` label and the action service's `resolve_target` output; the compose
gateway's ops-proxy is config-switched (`meridian_ops_target_mode`, default
`compose`, tests/CI/base-demo unaffected) to route fault injection to the
in-cluster NodePorts when the k8s overlay sets it to `k8s`; `restart-pod` is
the clean-success path since Meridian's fault lives in per-process
`MeridianState`, cleared by a pod recreate. Flow documented in
`deploy/k8s/README.md` ("Real remediation on Meridian").

**What it was:** Today there are two disjoint demos: (1) **Meridian** on docker-compose — best detection/diagnosis story, but remediation is `dry_run` (logs steps, simulated healthy, never touches infra); (2) the **kind cluster** (`deploy/k8s/`) — real pod remediation via the Kubernetes API, but only against a single `demo-app`, not Meridian. The user wants **real remediation on Meridian** ("real performance, not simulated healthy").

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
