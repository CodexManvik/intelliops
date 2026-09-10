# Agentic, Context-Grounded Runbook Author — Design

**Status:** Draft for review
**Date:** 2026-09-10
**Author:** IntelliOps team (CodexManvik) + Claude
**Supersedes behavior of:** PR #35 (AI-authored runbooks), as hardened by #46/#47/#48

---

## 1. Problem

The AI runbook author (governance service) drafts remediation runbooks **blind**.
`RunbookAuthor.draft(situation, hint)` sends the LLM only the situation's
`id`, `severity`, `signature`, and an optional freetext `hint`. It knows nothing
about:

- **what the target system is** — its services, how they depend on each other,
  what each does, which metrics matter;
- **what happened before** — which runbooks were tried for this kind of
  incident, which *worked*, and which a human *accepted or rejected*.

Two consequences:

1. **We can't explain its choices.** A draft appears with a rationale the model
   invented from a signature string. There is no grounding a reviewer can point
   at ("it chose restart because restart resolved this signature 4/5 times and a
   human rejected scale-service here last week").
2. **It doesn't learn.** The system already records outcomes and human
   decisions, but none of that feeds back into drafting. The same weak draft is
   produced whether the last ten attempts succeeded or failed.

## 2. Goals

- The author drafts with **context**: a description of the target system + the
  live incident + the **learned experience** for this signature.
- The author **learns from past actions** — its own and humans' — via
  **retrieval** (reading history into the prompt), not model training.
- The author **keeps a memory of its own decisions**: every draft is logged
  (chosen actions + cited facts), and its human disposition and eventual outcome
  are written back, so the *next* draft for a similar incident is better
  informed. Structured and durable (Postgres), not a free-text scratchpad.
- The author is a **tool-calling agent**: it gathers context by calling tools
  (like a function-calling agent) and then submits a typed runbook.
- **System-agnostic.** The target system that IntelliOps remediates is not yet
  decided (Meridian is being replaced). The context mechanism is **config-driven**
  and carries no target specifics in code.
- **Safety unchanged.** The submitted runbook still passes the closed action
  Literal, is forced to HITL, gets a server-assigned id, and requires human
  approval. Sandbox may remain off. The agent's context-gathering never touches
  the execution path.
- **Off-by-default preserved.** With no model wired, `NullRunbookAuthor` remains
  the default (no network, returns `None`), exactly as today.

## 3. Non-Goals

- **No fine-tuning / model training.** Learning is retrieval only.
- **No new remediation actions.** The closed `RemediationStep` Literal is
  unchanged. ("Tools" here means *LLM tool calls the author uses to think*, not
  new remediation verbs.)
- **No target-system implementation.** We ship the context *mechanism* and a
  documented, placeholder context file; the real target's content is filled in
  later when that app exists.
- **No change to detection, correlation, RCA ranking, or the execution path.**
- **No auto-registration.** A drafted runbook is always a *proposal*; a human
  approves it in governance.
- **No open internet / web-search access.** Considered and explicitly rejected:
  web content is untrusted input (a prompt-injection vector into a component that
  triggers production remediation), and it undercuts explainability — every fact
  the agent uses must be first-party and citable (curated system context, our own
  outcomes, audit trail, its own decision log). The closed 7-action vocabulary
  also means the agent needs no external discovery of *how* to remediate. The
  tool-calling design does leave the door open for **scoped, authenticated
  first-party tools later** (e.g. live Prometheus reads, an internal
  runbook/incident source) — added as bounded tools in this same framework, in
  the same trust class as the DB — but open web access stays out.

## 4. Background: what already exists (build on this)

- **`RunbookAuthor` protocol** in governance with `NullRunbookAuthor` (default)
  and `OpenAICompatibleRunbookAuthor` (single-shot chat/completions today).
  Constructed by `_make_runbook_author(settings)` in `services/governance/app.py`;
  gated by `runbook_author_mode == "openai" and llm_runbook_endpoint`.
- **Learning data already recorded, just unused by the author:**
  - `TrainingRecord` (`signature`, `playbook_id`, `result`, `worked`, `ts`) in
    the feedback **`TrainingStore`** (InMemory / File / Postgres). Per-signature
    worked/total is computed by `services/feedback/metrics.py::compute_metrics`.
  - `RemediationOutcome` (`result`, `health_after`, `steps`, `mode`, …) — the
    raw outcome an action produces.
  - `AuditRecord` (`actor`, `action`, `resource`, `decision`, `ts`,
    `correlation_id`) — records `propose` / `approve-proposal` / `reject`, i.e.
    **what humans accepted or rejected**.
- **`make_stores(settings)` already builds `training_store`** (Postgres when
  `STORE_BACKEND=postgres`) — governance can obtain it on `app.state` with a
  one-line addition. Shared-DB reads are consistent with ADR-014/015.
- **Governance already has** `audit_sink`, `playbook_store`, `approval_store`,
  `proposed_store` on `app.state`.
- **`gpt-oss-120b` on Groq supports OpenAI-style function calling** (`tools` +
  `tool_calls`), so the agentic loop is reachable on the current endpoint.

## 5. Design overview

Replace the single-shot draft with a **bounded tool-calling agent loop** inside
the governance service. Given a `Situation`, the author:

1. sends the LLM a system prompt (its role + rules + the closed action set) and
   the tool schemas;
2. the LLM issues **tool calls** to gather what it needs (system context, past
   outcomes, human decisions, incident details, the action list);
3. we execute each tool against local stores and return the result;
4. the LLM iterates until it calls the terminal tool `submit_runbook`, or a step
   budget is hit;
5. the submitted runbook is validated against the closed `Playbook` /
   `RemediationStep` schema (unchanged gate) and returned to
   `propose_playbook`, which forces HITL + assigns the id + stores the proposal
   **and records an `AuthorDecision`** (the draft's chosen actions + cited
   facts) so the agent can learn from this decision next time.

```
Situation ─▶ RunbookAuthorAgent.draft()
                 │  (LLM ⇄ tools loop, bounded)
                 ├─ get_system_context()      ← system_context.yaml (config)
                 ├─ get_incident_details(id)   ← the Situation in hand
                 ├─ get_past_outcomes(sig)     ← feedback TrainingStore (Postgres)
                 ├─ get_human_decisions(sig)   ← AuditRecords (audit_sink)
                 ├─ get_past_decisions(sig)    ← AuthorDecisionStore (Postgres)  ◀ NEW: its own memory
                 ├─ list_available_actions()   ← closed RemediationStep Literal
                 └─ submit_runbook(pb, why) ─▶ Playbook.model_validate (GATE)
                                                    │
                                    propose_playbook: force HITL + assign id
                                                    │       │
                                                    │       └─▶ AuthorDecisionStore.record(...)  ◀ NEW: log this decision
                                                    │
                                          ProposedPlaybook  (awaits human approve)
                                                    │
                          approve / reject  ──▶  AuthorDecisionStore.update_disposition(...)  ◀ NEW: close the loop
                                                    │
                          remediation outcome ──▶ AuthorDecisionStore.update_outcome(...)     ◀ NEW: did the draft work
```

**The self-referential memory (this arc's new capability).** Beyond retrieving
*remediation outcomes* and *human decisions*, the author now keeps and reads a
memory of **its own past drafting decisions**: what it chose for a signature,
which facts it cited, and — filled in later — whether a human accepted it and
whether it ultimately worked. Next time a similar signature appears, the agent
reads that memory (`get_past_decisions`) and drafts a *better-informed* runbook.
The memory is **structured** (not free-text the model appends to and re-obeys)
and lives in a **new Postgres store** (`AuthorDecisionStore`).

### 5.1 The tools

All read-only except `submit_runbook`. Each maps to data governance can reach
locally (no new network dependency).

| Tool | Args | Returns | Source |
|---|---|---|---|
| `get_system_context` | — | target system summary: services, dependency order, what each does, key metrics, per-action when-to-use notes | `system_context.yaml` (config) |
| `get_incident_details` | `situation_id` | the situation's metrics, member events, signature, severity | the `Situation` passed to `draft()` (no lookup — served from memory) |
| `get_past_outcomes` | `signature` | per-runbook worked/total + last N outcomes for this signature | feedback `TrainingStore` (+ metrics summary) |
| `get_human_decisions` | `signature` | prior proposals for this signature and their accept/reject decisions | `audit_sink` records |
| `get_past_decisions` | `signature` | **the agent's own prior drafting decisions** for this signature: actions chosen, facts cited, human disposition (accepted/rejected), and outcome (worked/failed) | **`AuthorDecisionStore`** (new, Postgres) |
| `list_available_actions` | — | the closed action vocabulary + one-line usage note per action | derived from `RemediationStep` Literal + a static notes map |
| `submit_runbook` | `playbook`, `rationale`, `cited_facts` | terminates the loop; the cited facts are logged with the decision | validated by the existing gate |

**Why these:** they cover the context axes from the goals — *system*
(`get_system_context`), *incident* (`get_incident_details`), *experience*
(`get_past_outcomes` + `get_human_decisions`), and now the agent's **own memory**
(`get_past_decisions`) — plus the action menu and the terminal submit. The agent
decides which to call; a good agent reads experience *and its own past
judgments* before drafting, which is the whole point. `submit_runbook` also
captures the facts the agent cited, so the decision it logs is self-explaining.

### 5.2 The system-context file (system-agnostic)

`get_system_context` reads a YAML file whose **schema is fixed** but whose
**content is target-specific and filled in later**. Placeholder shipped now.

Schema (documented in the file itself):

```yaml
system:
  name: ""                # e.g. "Payments API" — filled in per target
  summary: ""             # 1–3 sentences: what this system does
services:                 # the remediable workloads
  - name: ""              # k8s Deployment / logical service name
    role: ""              # what it does
    depends_on: []        # upstream service names (dependency order)
    key_metrics: []       # metric names that indicate this service's health
actions:                  # when-to-use guidance for the CLOSED action set
  restart: ""             # e.g. "recycle a wedged process / clear stuck state"
  scale: ""
  rollback_deploy: ""
  wait: ""
  patch_resource_limits: ""
  rollback_to_revision: ""
  patch_probe: ""
notes: ""                 # freeform operator guidance for the drafting agent
```

- **Location & delivery:** decided at plan time, but the leading option is a
  repo file mounted into governance via a ConfigMap (reviewable in PRs,
  editable without a rebuild). A missing/empty file is valid → the tool returns
  an "unconfigured" marker and the agent drafts from incident + experience
  alone. This is what keeps the feature *working* before the target exists.
- **Explainability:** because we author this file, every system fact the agent
  uses is one we can point to and defend.

### 5.3 The agent loop (bounded & fail-safe)

- **Budget:** at most `N` tool-call rounds (default small, e.g. 6) and the
  existing per-call timeout. Exceeding the budget without a `submit_runbook` →
  return `None` (no draft), same as any other failure.
- **429 / transport / invalid handling:** carried over from #48 — retry
  recoverable failures (rate-limit with backoff, one re-draft on invalid),
  never retry terminal failures. `max_tokens` cap retained.
- **Never raises:** any exception in the loop or a tool → logged → `draft()`
  returns `None`. The gate downstream already turns `None` into a clean 422.
- **The terminal gate is unchanged:** `submit_runbook`'s payload is validated
  by `Playbook.model_validate` (closed `RemediationStep` Literal). An
  out-of-catalog action is rejected exactly as today — tool-calling does not
  widen what can be submitted.

### 5.4 Explainability output

The proposal already carries a `rationale`. We additionally record, in the
existing audit trail for the proposal, **which tools the agent called and the
key facts returned** (e.g. "read past_outcomes: restart 4/5, scale 0/2; read
human_decisions: scale-service rejected 2026-09-03"). This is the artifact you
point at to explain *why* a runbook was drafted. (Exact storage — audit record
vs. a field on `ProposedPlaybook` — decided at plan time; leaning on an audit
record so `ProposedPlaybook` stays lean.)

### 5.5 The author's decision memory (`AuthorDecisionStore`) — this arc's core add

A new, durable, **structured** memory of the agent's own drafting decisions, so
"next time it knows more." Its lifecycle spans three events:

1. **On draft (`submit_runbook` → `propose_playbook`):** record an
   `AuthorDecision` — `signature`, `proposal_id`, the `actions` chosen, the
   `cited_facts` the agent gave, a short capped `note`, `ts`, and
   `disposition="pending"`, `outcome="unknown"`.
2. **On human approve/reject:** `update_disposition(proposal_id, "accepted"|"rejected", decided_by)`.
3. **On remediation outcome** (the approved runbook ran): `update_outcome(proposal_id, "worked"|"failed", health_after)`.

Then `get_past_decisions(signature)` returns these completed records, so a
future draft sees: *"for this signature I previously chose restart+scale, cited
'restart 4/5'; the human accepted it; it worked"* — or *"I chose scale, the
human rejected it."* That is the agent learning from itself.

**Contract (`AuthorDecision`, new):**

```
signature: str
proposal_id: str                 # links to the ProposedPlaybook
actions: list[str]               # the step actions chosen (closed-vocab strings)
cited_facts: list[str]           # what the agent said it based the draft on
note: str | None                 # short, length-capped model note (optional)
disposition: "pending"|"accepted"|"rejected"
outcome: "unknown"|"worked"|"failed"
decided_by: str | None
ts: datetime
```

**Store:** `AuthorDecisionStore` protocol with `InMemory` (tests) and `Postgres`
implementations, mirroring `TrainingStore`. New `author_decisions` table in
`common/db.py` (SQLAlchemy, JSONB `payload` like the others) + a new Alembic
migration `0005_author_decisions.py` (the migrate Job runs `alembic upgrade head`,
so a bare `Table` is not enough — a migration is required). On `app.state` in
governance via `make_stores`.

**Why structured, not a free-text memory file:** the agent's own past output is
untrusted when replayed into a later prompt (a poisoned or bad earlier note
could steer a future draft). Storing bounded, typed fields — and surfacing any
free-text `note` to the model **clearly labeled as prior, unverified reasoning,
never as instructions** — keeps the memory auditable and injection-resistant.
The closed action Literal + human approval remain the hard gates regardless.
A file-based memory (memory.md-style) was rejected: multi-pod/restart file
writes in k8s are racy and fragile; Postgres is the correct durable store here.

## 6. Learning model (retrieval, explicit)

"Learn from past actions" = the agent **retrieves and reads** three histories
for the incident's signature before drafting:

1. **Outcomes** (`get_past_outcomes`): what was tried and whether it *worked* —
   from the feedback `TrainingStore` the loop already fills. Biases the draft
   toward actions with a track record for this signature.
2. **Human decisions** (`get_human_decisions`): which prior proposals a human
   *accepted* or *rejected* for this signature — from audit records. Biases the
   draft away from things humans rejected and toward what they approved.
3. **Its own past decisions** (`get_past_decisions`): what the agent itself
   chose before, the facts it cited, and how those decisions turned out
   (accepted? worked?) — from `AuthorDecisionStore`. This is the
   self-referential loop: the agent's judgment improves because it can see how
   its earlier judgments fared.

Properties: deterministic given the same history, no training infrastructure,
fully inspectable, and it improves automatically as outcomes, human decisions,
and the agent's own recorded decisions accumulate — every remediation result,
every approve/reject, and every prior draft makes the next draft better.

## 7. Data flow & component changes (summary — details in the plan)

- **New:** `RunbookAuthorAgent` (tool-calling) alongside the existing adapters;
  a `SystemContextProvider` (reads the YAML); a small "author tools" module
  defining tool schemas + dispatch to local stores; **`AuthorDecisionStore`**
  (InMemory + Postgres).
- **Contracts:** `AuthorDecision` (new, §5.5); no change to `Playbook` /
  `RemediationStep`.
- **DB:** new `author_decisions` table in `common/db.py` + Alembic migration
  `0005_author_decisions.py`.
- **Governance wiring:** put `training_store` **and `author_decision_store`** on
  `app.state` (from `make_stores`); construct the agent with handles to
  `training_store`, `audit_sink`, `author_decision_store`, the context provider,
  and the closed action set. `propose_playbook` records a decision on draft;
  the **approve/reject routes update its disposition**; contract of
  `propose_playbook` (returns `ProposedPlaybook`) unchanged.
- **Outcome linkage:** when a remediation outcome lands for an approved
  AI-authored playbook, `AuthorDecisionStore.update_outcome` is called. Where
  this hook lives (governance consuming `remediation.outcomes`, vs. feedback
  writing it) is a plan-time decision — see open question 6.
- **Config:** `system_context.yaml` (placeholder) + a settings path for it +
  chart wiring to mount it. Off-by-default and the #46 key wiring unchanged.
- **Feedback service:** unchanged for reads (governance reads the shared
  training store directly; no new endpoint).

## 8. Safety & failure analysis

| Concern | Handling |
|---|---|
| AI submits an unsafe action | Closed `RemediationStep` Literal rejects it at `submit_runbook` — unchanged gate. |
| AI sets its own id / auto-approves | `propose_playbook` forces HITL + assigns id; approval is a separate human step. Unchanged. |
| Model unavailable / no key | `NullRunbookAuthor` default → `None`. Off-by-default. |
| System-context file missing/empty | Tool returns "unconfigured"; agent drafts from incident + experience; feature still works. |
| Loop never submits / loops forever | Bounded rounds + timeout → `None` → clean 422. |
| Rate limiting (Groq TPM) | #48 backoff carried over; tool-calling adds calls, so the budget is small and `max_tokens` capped. |
| Tool raises (e.g. DB blip) | Caught → that tool returns an error marker → agent proceeds or the loop ends → `None`. Never crashes governance. |
| Stale/poisoned history | History is the system's own audit + outcomes (not external input); retrieval only *informs* a draft a human still approves. |
| **Replaying the agent's own past text** | The decision memory is **structured** typed fields, not a free-text log the model appends to and re-obeys. Any free-text `note` is surfaced **labeled as prior, unverified reasoning, never as instructions**; the closed Literal + human approval still gate every submitted runbook. |
| **Decision-store write fails on draft** | Recording an `AuthorDecision` is best-effort around the proposal: a failed write is logged, the proposal still returns. The memory is an aid, not a correctness dependency. |
| **Disposition/outcome update missed** | An update that never lands leaves the record `pending`/`unknown` — the agent simply has less signal for that entry, never wrong signal. |

## 9. Explainable demo (once a target system exists)

With the target's `system_context.yaml` filled in, the story is:

1. A gap incident appears (no matching runbook).
2. Operator clicks "Draft a runbook with AI."
3. The agent calls `get_system_context` (knows the service + its deps),
   `get_incident_details`, `get_past_outcomes` (sees what worked),
   `get_human_decisions` (sees what was rejected), and `get_past_decisions`
   (sees what *it* chose before and how that turned out) — all recorded.
4. It submits a runbook whose rationale cites those facts; the decision is
   logged to `AuthorDecisionStore`.
5. A human approves → the decision's disposition becomes `accepted`; when the
   runbook runs, its outcome (`worked`/`failed`) is written back.
6. Next time the same signature recurs, the agent reads that completed decision
   ("last time I chose X, human accepted, it worked") plus the accumulated
   outcomes/human-decisions — and drafts a measurably better-informed runbook.
   **That closed loop is the learning, demonstrated on screen.**

(2–3 concrete, scripted use cases will be defined against the real target when
it's chosen — deliberately deferred, per the system-agnostic goal.)

## 10. Open questions for review

1. **System-context delivery:** repo YAML + ConfigMap mount (recommended) vs.
   baked file vs. a governance `/config/system-context` runtime endpoint. Any
   preference, or leave to the plan?
2. **Loop budget:** default max tool-call rounds (proposing 6) and overall
   timeout. Acceptable?
3. **Does `get_past_outcomes` / `get_past_decisions` read Postgres directly**
   (shared-DB, simplest) or must it go through a feedback HTTP endpoint for
   service-boundary cleanliness? (Design assumes direct shared-DB read, matching
   ADR-014/015.)
4. **Outcome linkage for the decision memory (§7):** should governance consume
   `remediation.outcomes` to call `update_outcome`, or should the write happen
   where outcomes are already handled (feedback)? Governance-consumes keeps the
   decision store fully owned by governance; feedback-writes avoids a new
   consumer. Preference?
5. **Scope of this arc:** ship the mechanism + decision memory + placeholder
   context now, or wait until the target system is chosen so the demo use cases
   land in the same PR? (The decision-memory loop is demonstrable even with a
   placeholder system context, since it learns from outcomes/decisions.)
6. **Author-trace vs. decision record:** the `AuthorDecision` now captures the
   cited facts + chosen actions, so a separate audit "trace" record (old Q2) may
   be redundant. Fold explainability entirely into `AuthorDecision`, or still
   emit an audit trace too?
```
