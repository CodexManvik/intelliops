# AI-authored runbooks (propose → approve) — Design Spec (PR C)

**Date:** 2026-09-04
**Owner:** Manvik
**Status:** design (architectural — new `ProposedPlaybook` contract, a `ProposedPlaybookStore`, a `RunbookAuthor` LLM adapter, and governance routes for the proposed→approved lifecycle). Final of a 3-PR arc (A: sandbox ✅ #33 · B: denylist + tier-2 vocab ✅ #34 · **C: AI-authored runbooks**).

**Depends on:** PRs A and B (both merged to master `664375d`). Branch PR C **off current master** — it relies on the tier-2 `RemediationStep` Literal (an AI-proposed playbook's steps validate against the *widened* closed set) and reuses the governance approval/RBAC/audit machinery unchanged.

**Companion:** `docs/sandbox-and-ai-runbooks-design-note.md` (the arc rationale).

## The problem

Today every playbook in the registry is human-authored and seeded (`load_seed_playbooks`, the YAML in `playbooks/`). When RCA diagnoses a situation for which no playbook matches, `surface_runbook` returns `None` (`services/rca/rank.py:122-126`, top hypothesis `suggested_runbook_id=None`) and the incident simply has no suggested remediation — a **gap**. The user wants the AI to help close that gap: draft a detailed, *typed* runbook for the situation, which a human then reviews and approves before it joins the live registry. The safety line is bright: **the AI proposes, a human disposes** — no AI-authored playbook ever executes without explicit human approval, and no AI can smuggle an unsafe action past the type system.

## Goal

Add a **propose → approve lifecycle** for AI-authored runbooks, entirely human-initiated:
- A human, seeing a gap on an incident, requests an AI draft ("Draft a runbook with AI").
- Governance calls a `RunbookAuthor` (LLM, fail-to-nothing) that returns a **typed `Playbook`** parsed via `model_validate` — so any unsafe/malformed action is rejected by the closed tier-2 `RemediationStep` Literal, `hitl_mode` is **forced to HITL**, and the `id` is **server-assigned**.
- The draft is stored as a `ProposedPlaybook` (status `proposed`) in a `ProposedPlaybookStore` — NOT the live registry.
- A human with `approve` permission reviews it and approves (→ the inner `Playbook` is `register()`-ed into the live `PlaybookStore`, status `approved`) or rejects (status `rejected`). Both are RBAC-gated and audited, exactly like the existing `decide_approval`/`graduate_playbook` flows.

## Key decisions (locked with the user)

1. **On-demand, human-initiated.** Authoring is triggered by a human via an endpoint, NOT automatically in the RCA diagnose hot path. RCA's role is unchanged — it already surfaces the gap (`suggested_runbook_id=None`); the console shows a "Draft a runbook with AI" affordance for such incidents. No LLM call is added to any consumer loop.
2. **Author lives in governance.** Governance owns the stores, the routes, and the approval/RBAC/audit machinery, so the whole proposed→approved lifecycle lives in one service. The `RunbookAuthor` adapter is co-located there. (It parallels RCA's `ExplanationProvider` in *pattern* — a deterministic/null default + an OpenAI-compatible LLM variant that falls back on any failure and never raises — but it is a governance adapter.)
3. **Distinct `ProposedPlaybook` contract.** A new contract wrapping the typed `Playbook` plus proposal metadata (id, status, proposed_by, rationale, source_situation_id, ts). Keeps "proposed" cleanly separate from the live registry; the inner `Playbook` still validates via the closed `RemediationStep` Literal, so unsafe actions are rejected at parse time.
4. **In-memory `ProposedPlaybookStore`.** Mirrors `InMemoryPlaybookStore` / `InMemoryApprovalStore`, held in `governance.app.state`, with a reset endpoint for tests. File/Postgres persistence deferred (like the other stores).
5. **Reuse the existing `approve`/`reject` RBAC actions** on `playbook:*` — no new RBAC role or action. An `approver` (e.g. `oncall-alice`) approves/rejects proposals, same as they decide approvals today. Drafting itself reuses `approve` (a reviewer drafting a candidate for their own review). No `rbac_policy.yaml` change required.
6. **The AI never sets safety-critical fields.** On parse, `hitl_mode` is FORCED to `HitlMode.HITL` (never AUTO/DISABLED — an AI draft always requires a human at execution time too), the `id` is server-assigned (the AI cannot overwrite an existing playbook), and `reversible` defaults conservatively. `model_validate` rejecting any out-of-set action is the load-bearing guarantee — inherited from PR B's closed tier-2 Literal for free.

## Non-goals / constraints

- **No automatic authoring**, no LLM in any consumer hot path (decision 1).
- **No new RBAC** (decision 5). No change to `policies/rbac_policy.yaml`.
- **No execution-path change.** An approved AI-authored playbook enters the SAME `PlaybookStore.register` and is thereafter indistinguishable from a human playbook to `execute_remediation` — it goes through every existing gate (disabled/reversible/RBAC/**denylist**/**sandbox**/HITL). PR C adds only the *authoring + approval* front-door; it does not touch `remediate.py`.
- **Test-safe default.** The LLM author is off by default — a `NullRunbookAuthor` (or the template author) returns "no draft" so the base suite + CI never call an endpoint. The real author is opt-in via the same LLM config surface RCA already exposes (`/config/llm` pattern), or a governance `llm_*` setting. The base ~463-test suite stays green; `ruff` clean; `npm --prefix frontend run build` clean.
- **Fail-to-nothing.** `RunbookAuthor` never raises: any LLM failure (unreachable, non-200, non-JSON, missing content, unparseable/invalid Playbook, unsafe action) → returns `None` (no proposal), the endpoint responds with a clear "could not draft" (HTTP 422/503), and nothing is stored. Mirrors `OpenAICompatibleExplanationProvider`'s fallback discipline.
- **The AI cannot approve its own work.** The propose endpoint stores a `proposed` record only; a human must call the approve endpoint. There is no path from draft → live registry that doesn't pass through the RBAC-gated approve route.

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~463 base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **Safety invariants (must hold):** an AI-proposed playbook's `steps` validate against the closed tier-2 `RemediationStep` Literal (unsafe action → parse fails → no proposal); a proposal's `hitl_mode` is always HITL; the `id` is server-assigned; the ONLY path to the live registry is the RBAC-gated approve route; approve/reject are audited.
- **Env:** `uv sync --extra ml --extra k8s` at setup (a bare `uv sync` fails collection — missing `river`/`kubernetes`).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/ai-authored-runbooks` off master. PR; user merges. Never merge to master.
- **Shared files:** `common/contracts.py`, `common/interfaces.py`, `common/config.py`, `services/governance/app.py`, `services/governance/adapters/`, `frontend/src/data/types.ts`, and a console view for the proposal queue.

---

## Design

### 1. Contract: `ProposedPlaybook` (`common/contracts.py`)

```python
class ProposedPlaybookStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProposedPlaybook(BaseModel):
    id: str                      # server-assigned (e.g. "prop-" + uuid)
    playbook: Playbook           # the typed draft — its steps validate via the closed Literal
    status: ProposedPlaybookStatus = ProposedPlaybookStatus.PROPOSED
    proposed_by: str             # "runbook-author" (the LLM adapter) or the requesting human
    rationale: str | None = None # the AI's short justification, if any
    source_situation_id: str | None = None
    decided_by: str | None = None
    ts: datetime
```

The inner `playbook: Playbook` is the safety anchor: constructing a `ProposedPlaybook` from LLM output runs `Playbook.model_validate`, which runs `RemediationStep.model_validate` on each step, which rejects any action outside the closed tier-2 set. An unsafe draft cannot become a `ProposedPlaybook`.

### 2. Interface: `RunbookAuthor` (`common/interfaces.py`)

```python
@runtime_checkable
class RunbookAuthor(Protocol):
    """Drafts a typed Playbook for a gap. Returns None when it cannot (fail-to-
    nothing) — never raises. The returned Playbook's hitl_mode/id are normalized
    by the caller (forced HITL, server-assigned id)."""

    def draft(self, situation: Situation, hint: str | None = None) -> tuple[Playbook, str | None] | None: ...
    # returns (draft_playbook, rationale) or None
```

### 3. Adapters (`services/governance/adapters/runbook_author.py`, new)

- **`NullRunbookAuthor`** (default): `draft(...) -> None`. No LLM, always "no draft" — keeps the base demo/tests off the network. This is the config-switched default.
- **`OpenAICompatibleRunbookAuthor`**: mirrors `OpenAICompatibleExplanationProvider` exactly — sync `httpx.Client`, an SRE system prompt instructing the model to return a runbook as **strict JSON** matching the `Playbook` shape (steps limited to the allowed actions, which the prompt enumerates), and the same fallback discipline:
  1. POST to `{base}/chat/completions`; on `httpx.HTTPError` / non-200 / non-JSON / missing content → return `None`.
  2. Parse the model's content as JSON; `Playbook.model_validate(parsed)` inside a `try/except ValidationError` → on failure return `None` (this is where an unsafe action or malformed draft is rejected).
  3. Return `(playbook, rationale)`.
  It NEVER raises. The prompt explicitly lists the 7 allowed actions and says "use only these; any other action will be rejected" — but the type system, not the prompt, is the guarantee.

### 4. Store: `ProposedPlaybookStore` (`services/governance/adapters/proposed_store.py`, new)

```python
class InMemoryProposedPlaybookStore:
    def add(self, proposal: ProposedPlaybook) -> None: ...
    def get(self, proposal_id: str) -> ProposedPlaybook | None: ...
    def list(self, status: ProposedPlaybookStatus | None = None) -> list[ProposedPlaybook]: ...
    def set_status(self, proposal_id: str, status: ProposedPlaybookStatus, decided_by: str) -> ProposedPlaybook | None: ...
```

Held in `governance.app.state.proposed_store`, initialized in `_init_state`. Mirrors `InMemoryApprovalStore` (a `_by_id` dict + a `clear()` for reset).

### 5. Config (`common/config.py`)

The LLM settings live on the shared `Settings` (`env_prefix="INTELLIOPS_"`), which governance already reads via `get_settings()` — the RCA explanation LLM is configured there as `llm_explanation_endpoint`/`llm_explanation_model`/`llm_explanation_timeout_seconds`/`llm_explanation_api_key` (`config.py:69-72`). Add governance-scoped author fields to the SAME class, mirroring that block exactly:
```python
    runbook_author_mode: str = "off"                 # "off" | "openai"
    llm_runbook_endpoint: str = ""                   # empty = NullRunbookAuthor, no network
    llm_runbook_model: str = "gpt-4o-mini"
    llm_runbook_timeout_seconds: float = 10.0
    llm_runbook_api_key: str = ""                    # runtime-supplied like RCA; not persisted in plaintext
```
Default `runbook_author_mode="off"` (and/or empty `llm_runbook_endpoint`) → `NullRunbookAuthor`.

### 6. Governance routes (`services/governance/app.py`)

Mirror the existing route shapes (get-then-404 → RBAC-or-403 → mutate → audit → return). A `_make_runbook_author(settings)` factory builds the adapter into `app.state.runbook_author` (default `NullRunbookAuthor`).

- **`POST /playbooks/proposed`** — body carries the source `Situation` (+ optional `hint`, + `requested_by`). RBAC-check `requested_by`/`approve` (403 if not). Call `app.state.runbook_author.draft(situation, hint)`:
  - `None` → HTTP 422 `{"drafted": false, "reason": "author could not produce a valid runbook"}` (no store write).
  - `(playbook, rationale)` → normalize: force `playbook.hitl_mode = HITL`, assign a fresh `id` (`"aiprop-" + situation.signature`-ish), build a `ProposedPlaybook(id="prop-"+uuid, playbook=normalized, proposed_by="runbook-author", rationale=..., source_situation_id=situation.id, ts=now)`, `proposed_store.add(...)`, audit `action="propose"`, return the `ProposedPlaybook`.
- **`GET /playbooks/proposed`** — `proposed_store.list(status=...)` (optional `status` query).
- **`GET /playbooks/proposed/{id}`** — get-or-404.
- **`POST /playbooks/proposed/{id}/approve`** — body `{decided_by}`. Get-or-404; RBAC-check `decided_by`/`approve`/`playbook:{inner.id}` or 403; `proposed_store.set_status(id, APPROVED, decided_by)`; **`app.state.playbook_store.register(proposal.playbook)`** (the inner typed Playbook enters the live registry); audit `action="approve-proposal"`, `decision="allow"`; return the updated `ProposedPlaybook`.
- **`POST /playbooks/proposed/{id}/reject`** — body `{decided_by}`. Get-or-404; RBAC-check `reject` or 403; `set_status(id, REJECTED, decided_by)`; audit `action="reject-proposal"`; return updated. Does NOT register.
- **`POST /reset-proposed`** — test helper, clears the store (mirrors `reset-approvals`).

Every mutating route writes an `AuditRecord` (correlation_id = the proposal id or source situation id), exactly like `graduate_playbook`.

### 7. Frontend (`frontend/src/data/types.ts` + a proposal-queue view)

- `ProposedPlaybook` type mirroring the contract (id, playbook, status, proposed_by, rationale, source_situation_id, decided_by, ts).
- On an incident with no suggested runbook (the gap), a **"Draft a runbook with AI"** action that POSTs to `/playbooks/proposed` with the situation.
- A **proposals queue** (a small view or a panel): lists `proposed` items with the drafted steps/rationale, and Approve / Reject buttons hitting the respective routes. Honest, real-data — shows exactly what the AI drafted, with the human as the gate. In mock mode, a seeded example proposal so the queue is visible without an LLM.

---

## Acceptance criteria

1. **Safety: unsafe drafts are rejected at parse.** Unit test: `OpenAICompatibleRunbookAuthor` fed a mocked LLM response whose JSON contains an out-of-set action (e.g. `"delete"`) returns `None` (the `Playbook.model_validate` fails). A valid draft returns `(Playbook, rationale)`.
2. **Fail-to-nothing:** the author returns `None` on every failure path (unreachable / non-200 / non-JSON / missing content / invalid Playbook) and NEVER raises. `NullRunbookAuthor.draft` always returns `None`.
3. **The propose route stores a proposal, never the live registry:** unit/integration test — `POST /playbooks/proposed` with a stub author returning a valid draft creates a `ProposedPlaybook` (status `proposed`) in the proposed store and does NOT call `playbook_store.register`. A `None`-returning author → 422, nothing stored.
4. **hitl forced + id server-assigned:** the stored proposal's `playbook.hitl_mode == HITL` regardless of what the draft said, and its `playbook.id`/proposal `id` are server-assigned (an AI-supplied id cannot overwrite an existing playbook).
5. **Approval registers; reject does not:** `POST …/approve` (as an `approver`) sets status `approved` AND calls `playbook_store.register(inner)` (the playbook is now live), audited; `POST …/reject` sets status `rejected`, does NOT register, audited. Both 404 on unknown id.
6. **RBAC-gated, reusing approve/reject:** approve/reject by an actor lacking the permission → 403 (mirror the `decide_approval` 404-before-403 ordering). No `rbac_policy.yaml` change.
7. **Test-safe default:** `runbook_author_mode` defaults `"off"` → `NullRunbookAuthor`; the base suite + CI never hit an endpoint; ~463 stays green; ruff + frontend build clean.
8. **(Manual, documented)** with the LLM configured, a human on a gap incident clicks "Draft a runbook with AI", sees the AI's typed draft in the proposals queue, and Approve moves it into the live registry (where it is thereafter subject to every execution gate incl. sandbox + denylist). Documented (a short governance/README section).

## Suggested task ordering (for the plan)

1. **Contracts + interface:** `ProposedPlaybookStatus`, `ProposedPlaybook`, the `RunbookAuthor` Protocol, `runbook_author_mode`/`llm_runbook_*` config. Unit tests: `ProposedPlaybook` validates; an inner playbook with an unsafe action fails to construct. (Green — nothing uses them yet.)
2. **Adapters:** `NullRunbookAuthor` + `OpenAICompatibleRunbookAuthor` (mirror `explanation_provider.py`'s structure + fallback discipline). Unit tests: null returns None; the LLM author returns None on each failure path incl. an unsafe-action JSON; a valid JSON returns a typed draft. (The safety heart — fully testable with a stubbed httpx client, no network.)
3. **Store:** `InMemoryProposedPlaybookStore` + a unit test (add/get/list/set_status + reset).
4. **Governance routes:** the 5 routes + the `_make_runbook_author` factory + `app.state` wiring, mirroring `decide_approval`/`graduate_playbook`. Integration tests (FastAPI TestClient): propose-stores-not-registry, approve-registers, reject-doesn't, RBAC 403s, 404s, hitl-forced, 422-on-None. (The lifecycle — reuse the existing governance test client style.)
5. **Frontend + docs:** the `ProposedPlaybook` type, the "Draft with AI" affordance on a gap incident, the proposals queue view with Approve/Reject, a mock proposal; a governance/README section on the propose→approve flow + the safety guarantees. Build + final gates.

Rationale: contracts first (green), then the author adapter (the safety-critical parse-and-validate, stub-tested), then the store, then the routes (the lifecycle, integration-tested), then the UI + docs — each independently testable, the "AI proposes / human disposes / type system guards" invariant provable at every step.
