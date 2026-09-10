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
   `propose_playbook`, which forces HITL + assigns the id + stores the proposal.

```
Situation ─▶ RunbookAuthorAgent.draft()
                 │  (LLM ⇄ tools loop, bounded)
                 ├─ get_system_context()      ← system_context.yaml (config)
                 ├─ get_incident_details(id)   ← the Situation in hand
                 ├─ get_past_outcomes(sig)     ← feedback TrainingStore (Postgres)
                 ├─ get_human_decisions(sig)   ← AuditRecords (audit_sink)
                 ├─ list_available_actions()   ← closed RemediationStep Literal
                 └─ submit_runbook(pb, why) ─▶ Playbook.model_validate (GATE)
                                                    │
                                    propose_playbook: force HITL + assign id
                                                    │
                                          ProposedPlaybook  (awaits human approve)
```

### 5.1 The tools

All read-only except `submit_runbook`. Each maps to data governance can reach
locally (no new network dependency).

| Tool | Args | Returns | Source |
|---|---|---|---|
| `get_system_context` | — | target system summary: services, dependency order, what each does, key metrics, per-action when-to-use notes | `system_context.yaml` (config) |
| `get_incident_details` | `situation_id` | the situation's metrics, member events, signature, severity | the `Situation` passed to `draft()` (no lookup — served from memory) |
| `get_past_outcomes` | `signature` | per-runbook worked/total + last N outcomes for this signature | feedback `TrainingStore` (+ metrics summary) |
| `get_human_decisions` | `signature` | prior proposals for this signature and their accept/reject decisions | `audit_sink` records |
| `list_available_actions` | — | the closed action vocabulary + one-line usage note per action | derived from `RemediationStep` Literal + a static notes map |
| `submit_runbook` | `playbook`, `rationale` | terminates the loop | validated by the existing gate |

**Why these:** they are exactly the three context axes from the goals —
*system* (`get_system_context`), *incident* (`get_incident_details`), and
*experience* (`get_past_outcomes` + `get_human_decisions`) — plus the action
menu and the terminal submit. The agent decides which to call; a good agent
reads experience before drafting, which is the whole point.

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

## 6. Learning model (retrieval, explicit)

"Learn from past actions" = the agent **retrieves and reads** two histories for
the incident's signature before drafting:

1. **Outcomes** (`get_past_outcomes`): what was tried and whether it *worked* —
   from the feedback `TrainingStore` the loop already fills. This biases the
   draft toward actions with a track record for this signature.
2. **Human decisions** (`get_human_decisions`): which prior proposals a human
   *accepted* or *rejected* for this signature — from audit records. This biases
   the draft away from things humans have rejected and toward what they've
   approved.

Properties: deterministic given the same history, no training infrastructure,
fully inspectable (the retrieved facts are shown in the audit trail), and it
improves automatically as more outcomes and decisions accumulate — every
approve/reject and every remediation result makes the next draft better.

## 7. Data flow & component changes (summary — details in the plan)

- **New:** `RunbookAuthorAgent` (tool-calling) alongside the existing adapters;
  a `SystemContextProvider` (reads the YAML); a small "author tools" module
  defining tool schemas + dispatch to local stores.
- **Contracts:** possibly a lightweight `AuthorTrace` (tools called + facts) for
  the audit trail; no change to `Playbook` / `RemediationStep`.
- **Governance wiring:** put `training_store` on `app.state` (from `make_stores`);
  construct the agent with handles to `training_store`, `audit_sink`, the
  context provider, and the closed action set; `propose_playbook` unchanged in
  contract (still returns a `ProposedPlaybook`).
- **Config:** `system_context.yaml` (placeholder) + a settings path for it +
  chart wiring to mount it. Off-by-default and the #46 key wiring unchanged.
- **Feedback service:** unchanged (governance reads the shared training store
  directly; no new endpoint).

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

## 9. Explainable demo (once a target system exists)

With the target's `system_context.yaml` filled in, the story is:

1. A gap incident appears (no matching runbook).
2. Operator clicks "Draft a runbook with AI."
3. The agent calls `get_system_context` (knows the service + its deps),
   `get_incident_details`, `get_past_outcomes` (sees what worked),
   `get_human_decisions` (sees what was rejected) — visible in the audit trail.
4. It submits a runbook whose rationale cites those facts.
5. A human approves; it enters the registry.
6. Next time the same signature recurs, the accumulated outcomes/decisions make
   the draft measurably better — that is the learning, demonstrated.

(2–3 concrete, scripted use cases will be defined against the real target when
it's chosen — deliberately deferred, per the system-agnostic goal.)

## 10. Open questions for review

1. **System-context delivery:** repo YAML + ConfigMap mount (recommended) vs.
   baked file vs. a governance `/config/system-context` runtime endpoint. Any
   preference, or leave to the plan?
2. **Author trace storage:** dedicated `AuditRecord`(s) (leaning this way) vs. a
   new field on `ProposedPlaybook`. Preference?
3. **Loop budget:** default max tool-call rounds (proposing 6) and overall
   timeout. Acceptable?
4. **Does `get_past_outcomes` read Postgres directly** (shared-DB, simplest) or
   must it go through a feedback HTTP endpoint for service-boundary cleanliness?
   (Design assumes direct shared-DB read.)
5. **Scope of this arc:** ship the mechanism + placeholder context now, or wait
   until the target system is chosen so the demo use cases land in the same PR?
```
