# Agentic, Context-Grounded Runbook Author — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the AI runbook author into a bounded tool-calling agent that gathers first-party context (curated system description, live incident, and three learned histories — remediation outcomes, human decisions, and its own past drafting decisions) before drafting, records each decision to a durable store, and closes the loop when the human decides and the runbook runs — so the next draft for a similar incident is better informed.

**Architecture:** A new `RunbookAuthorAgent` runs an LLM⇄tools loop inside the governance service. Read-only tools serve first-party data (a config `system_context.yaml`, the in-hand `Situation`, the feedback `TrainingStore`, `AuditRecord`s, and a new `AuthorDecisionStore`); the terminal `submit_runbook` tool's payload passes the unchanged closed-`Playbook` validation gate. Each draft logs an `AuthorDecision`; approve/reject updates its disposition; a new governance consumer of `remediation.outcomes` writes back the outcome. Off-by-default (`NullRunbookAuthor`) and every safety gate are unchanged.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy Core + Alembic, httpx (OpenAI-compatible chat/completions with `tools`), Redis Streams bus, Postgres. Groq `gpt-oss-120b`.

**Spec:** `docs/superpowers/specs/2026-09-10-agentic-context-grounded-runbook-author-design.md`

## Global Constraints

- **Off-by-default.** `NullRunbookAuthor` stays the default; the agent runs only when `runbook_author_mode == "openai" and llm_runbook_endpoint` is set. No network or model import at module load.
- **Closed action vocabulary unchanged.** The `RemediationStep.action` Literal (`restart`, `scale`, `rollback_deploy`, `wait`, `patch_resource_limits`, `rollback_to_revision`, `patch_probe`) is NOT modified. `submit_runbook` output is validated by `Playbook.model_validate` exactly as today; out-of-catalog actions are rejected.
- **Never raises.** Any failure in the agent loop, a tool, or a store write returns `None` from `draft()` (→ existing clean 422). Decision-store writes/updates are best-effort: a failure is logged, never crashes the request or the consumer.
- **Safety posture unchanged.** Forced HITL, server-assigned id, human approve/reject, denylist, sandbox-optional — all unchanged. The agent's context-gathering never touches the execution path.
- **The agent's own past free-text is untrusted.** Any stored free-text `note` replayed to the model is labeled as prior, unverified reasoning — never as instructions.
- **First-party inputs only.** No web/internet access (explicit non-goal). All tool data is local/first-party.
- **Reads go direct to shared Postgres** (ADR-014/015). No new feedback HTTP endpoint.
- **Retrieval, not training.** No fine-tuning; learning is reading history into the prompt.
- **Every commit ends with:** `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Slim-boundary (ADR-022) holds:** importing `services.governance.app` must not import heavy ML deps. The agent uses only httpx + stdlib.

---

### Task 1: `AuthorDecision` contract + `AuthorDecisionStore` (InMemory) + tests

**Files:**
- Modify: `common/contracts.py` (add `AuthorDecision`)
- Create: `services/governance/adapters/author_decision_store.py` (InMemory impl; Postgres added in Task 2)
- Create: `common/interfaces.py` addition — `AuthorDecisionStore` Protocol
- Test: `services/governance/tests/test_author_decision_store.py`

**Interfaces:**
- Produces: `AuthorDecision` model; `InMemoryAuthorDecisionStore` with methods `record(decision) -> None`, `by_signature(signature) -> list[AuthorDecision]`, `update_disposition(proposal_id, disposition, decided_by) -> None`, `update_outcome(playbook_id, outcome, health_after) -> None`. Protocol `AuthorDecisionStore` mirrors these.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# services/governance/tests/test_author_decision_store.py
from datetime import UTC, datetime
from common.contracts import AuthorDecision
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore


def _decision(**kw):
    base = dict(
        signature="sig-x",
        proposal_id="prop-1",
        playbook_id="ai-sig-x-abc123",
        actions=["restart", "scale"],
        cited_facts=["restart worked 4/5 for sig-x"],
        note=None,
        disposition="pending",
        outcome="unknown",
        decided_by=None,
        ts=datetime.now(UTC),
    )
    base.update(kw)
    return AuthorDecision(**base)


def test_record_and_query_by_signature():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    got = s.by_signature("sig-x")
    assert len(got) == 1
    assert got[0].actions == ["restart", "scale"]
    assert s.by_signature("other") == []


def test_update_disposition_by_proposal_id():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    s.update_disposition("prop-1", "accepted", "oncall-alice")
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "accepted"
    assert d.decided_by == "oncall-alice"


def test_update_outcome_by_playbook_id():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    s.update_outcome("ai-sig-x-abc123", "worked", "healthy")
    d = s.by_signature("sig-x")[0]
    assert d.outcome == "worked"


def test_updates_are_noops_when_no_match():
    s = InMemoryAuthorDecisionStore()
    s.record(_decision())
    s.update_disposition("nope", "accepted", "x")   # no matching proposal_id
    s.update_outcome("nope", "worked", "healthy")   # no matching playbook_id
    d = s.by_signature("sig-x")[0]
    assert d.disposition == "pending" and d.outcome == "unknown"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/governance/tests/test_author_decision_store.py -v`
Expected: FAIL (`AuthorDecision` and store do not exist yet).

- [ ] **Step 3: Add the `AuthorDecision` contract**

In `common/contracts.py`, after `ProposedPlaybook` (near the proposed-playbook models). Use plain `str` fields for `disposition`/`outcome` (validated by allowed values in code) to avoid over-engineering enums for a 3-value field; if the codebase prefers enums (it uses them for `HitlMode`/`RemediationResult`), add `AuthorDecisionDisposition`/`AuthorDecisionOutcome` str-enums instead — match the surrounding style.

```python
class AuthorDecision(BaseModel):
    """A record of one AI drafting decision — the author's own memory.

    Recorded when a runbook is drafted (disposition="pending", outcome="unknown");
    disposition is updated on human approve/reject; outcome is updated when the
    approved runbook runs. `get_past_decisions` reads these back so the agent
    learns from its own prior judgments. `note` is model free-text — treated as
    untrusted when replayed (surfaced as prior/unverified reasoning, never as
    instructions)."""

    signature: str
    proposal_id: str
    playbook_id: str            # the ai-<sig>-<uuid> id; links to RemediationOutcome.playbook_id
    actions: list[str] = Field(default_factory=list)
    cited_facts: list[str] = Field(default_factory=list)
    note: str | None = None
    disposition: str = "pending"     # "pending" | "accepted" | "rejected"
    outcome: str = "unknown"         # "unknown" | "worked" | "failed"
    decided_by: str | None = None
    ts: datetime
```

- [ ] **Step 4: Add the Protocol**

In `common/interfaces.py`, alongside the other store protocols:

```python
class AuthorDecisionStore(Protocol):
    def record(self, decision: "AuthorDecision") -> None: ...
    def by_signature(self, signature: str) -> list["AuthorDecision"]: ...
    def update_disposition(self, proposal_id: str, disposition: str, decided_by: str) -> None: ...
    def update_outcome(self, playbook_id: str, outcome: str, health_after: str) -> None: ...
```
(Import `AuthorDecision` under `TYPE_CHECKING` following the file's existing convention.)

- [ ] **Step 5: Implement `InMemoryAuthorDecisionStore`**

```python
# services/governance/adapters/author_decision_store.py
"""AuthorDecisionStore: the runbook author's memory of its own drafting decisions.

InMemory (tests) here; Postgres in the same module (Task 2). Never raises on a
missing target — updates are no-ops when nothing matches (a missed update just
leaves less signal, never wrong signal)."""

from __future__ import annotations

from common.contracts import AuthorDecision


class InMemoryAuthorDecisionStore:
    def __init__(self) -> None:
        self._items: list[AuthorDecision] = []

    def record(self, decision: AuthorDecision) -> None:
        self._items.append(decision)

    def by_signature(self, signature: str) -> list[AuthorDecision]:
        return [d for d in self._items if d.signature == signature]

    def update_disposition(self, proposal_id: str, disposition: str, decided_by: str) -> None:
        for i, d in enumerate(self._items):
            if d.proposal_id == proposal_id:
                self._items[i] = d.model_copy(
                    update={"disposition": disposition, "decided_by": decided_by}
                )

    def update_outcome(self, playbook_id: str, outcome: str, health_after: str) -> None:
        for i, d in enumerate(self._items):
            if d.playbook_id == playbook_id:
                self._items[i] = d.model_copy(update={"outcome": outcome})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest services/governance/tests/test_author_decision_store.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add common/contracts.py common/interfaces.py services/governance/adapters/author_decision_store.py services/governance/tests/test_author_decision_store.py
git commit -m "feat(governance): AuthorDecision contract + in-memory decision store"
```

---

### Task 2: `PostgresAuthorDecisionStore` + `author_decisions` table + Alembic migration + wire into `make_stores`

**Files:**
- Modify: `common/db.py` (add `author_decisions` Table on `METADATA`)
- Create: `alembic/versions/0005_author_decisions.py`
- Modify: `services/governance/adapters/author_decision_store.py` (add Postgres impl)
- Modify: `common/stores.py` (add `author_decision_store` to `Stores` + both `make_stores` branches)
- Test: `services/governance/tests/test_author_decision_store.py` (add a Postgres-shape unit test using a sqlite-or-skip guard consistent with other store tests; if the repo has no in-process DB test harness, assert the SQL builder shape and leave live-DB verification to compose-smoke — match how `test`-suite covers `PostgresTrainingStore`)

**Interfaces:**
- Consumes: `common.db.METADATA`, `to_payload`/`from_payload`, `make_engine`.
- Produces: `PostgresAuthorDecisionStore(engine)` with the same 4 methods; `Stores.author_decision_store`.

- [ ] **Step 1: Inspect the existing pattern**

Read `common/db.py` `training_records` Table (lines ~49-61) and `services/feedback/adapters/training_store.py::PostgresTrainingStore` — mirror them exactly (JSONB `payload` column + typed query columns).

- [ ] **Step 2: Add the table to `common/db.py`**

```python
author_decisions = Table(
    "author_decisions",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("signature", String, nullable=False, index=True),
    Column("proposal_id", String, nullable=False, index=True),
    Column("playbook_id", String, nullable=False, index=True),
    Column("disposition", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
)
```
(Use the same column types/imports already used by `training_records`; if the file uses a portable JSON type rather than `JSONB`, match that.)

- [ ] **Step 3: Write the Alembic migration**

`alembic/versions/0005_author_decisions.py`, `down_revision = "0004"` (verify the latest head with `uv run alembic heads`; chain from it). `upgrade()` creates `author_decisions` with the columns above + indexes on `signature`, `proposal_id`, `playbook_id`. `downgrade()` drops it. Mirror `0003_model_artifacts.py` for style.

- [ ] **Step 4: Implement `PostgresAuthorDecisionStore`**

Add to `author_decision_store.py`. `record` inserts (typed columns + `payload=to_payload(decision)`). `by_signature` selects `payload` where `signature=:sig` ordered by `id`, returns `from_payload(row.payload, AuthorDecision)`. `update_disposition` runs an UPDATE on rows matching `proposal_id`, setting the `disposition` column AND rewriting `payload` (read-modify-write within one transaction so the JSON payload and typed column stay consistent). `update_outcome` likewise matches `playbook_id`. Errors on `record` propagate to the caller (caller wraps best-effort); `by_signature` failure should be caught by the tool layer (Task 4), not here.

- [ ] **Step 5: Wire into `make_stores`**

Add `author_decision_store: object` to the `Stores` dataclass. In the postgres branch: `author_decision_store=PostgresAuthorDecisionStore(engine)`. In the file/default branch: `author_decision_store=InMemoryAuthorDecisionStore()` (no file impl needed — the in-memory store is acceptable for the non-postgres dev/test path, matching how some other stores degrade; note this explicitly in a comment).

- [ ] **Step 6: Run the full store + stores tests**

Run: `uv run pytest services/governance/tests/test_author_decision_store.py common/ -v` and `uv run pytest -k stores -v`
Expected: PASS. Also `uv run alembic heads` shows a single head at 0005.

- [ ] **Step 7: Commit**

```bash
git add common/db.py common/stores.py alembic/versions/0005_author_decisions.py services/governance/adapters/author_decision_store.py services/governance/tests/test_author_decision_store.py
git commit -m "feat(governance): Postgres author-decision store + 0005 migration + make_stores wiring"
```

---

### Task 3: `SystemContextProvider` + placeholder `system_context.yaml` + tests

**Files:**
- Create: `services/governance/adapters/system_context.py`
- Create: `config/system_context.yaml` (placeholder, documented schema)
- Modify: `common/config.py` (add `system_context_path: str = "config/system_context.yaml"`)
- Test: `services/governance/tests/test_system_context.py`

**Interfaces:**
- Produces: `SystemContextProvider(path)` with `load() -> dict | None` (returns parsed dict, or `None`/"unconfigured" marker when file missing/empty/all-placeholder) and `summarize() -> str` (a compact text block for the tool result). Never raises (a malformed file → treated as unconfigured, logged).

- [ ] **Step 1: Write the failing test**

```python
# services/governance/tests/test_system_context.py
from services.governance.adapters.system_context import SystemContextProvider


def test_missing_file_is_unconfigured(tmp_path):
    p = SystemContextProvider(str(tmp_path / "nope.yaml"))
    assert p.load() is None
    assert "unconfigured" in p.summarize().lower()


def test_placeholder_file_is_unconfigured(tmp_path):
    f = tmp_path / "sc.yaml"
    f.write_text('system:\n  name: ""\n  summary: ""\nservices: []\n')
    p = SystemContextProvider(str(f))
    assert p.load() is None  # all-empty placeholder = unconfigured


def test_populated_file_summarizes(tmp_path):
    f = tmp_path / "sc.yaml"
    f.write_text(
        'system:\n  name: "Payments API"\n  summary: "Handles card auth."\n'
        'services:\n  - name: "auth-svc"\n    role: "authorizes"\n    depends_on: ["db"]\n'
        '    key_metrics: ["error_rate"]\n'
    )
    p = SystemContextProvider(str(f))
    got = p.load()
    assert got["system"]["name"] == "Payments API"
    s = p.summarize()
    assert "Payments API" in s and "auth-svc" in s


def test_malformed_file_is_unconfigured_not_raise(tmp_path):
    f = tmp_path / "sc.yaml"
    f.write_text("::: not: valid: yaml: [")
    p = SystemContextProvider(str(f))
    assert p.load() is None
    assert p.summarize()  # does not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/governance/tests/test_system_context.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the provider**

```python
# services/governance/adapters/system_context.py
"""SystemContextProvider: reads the curated, system-agnostic description of the
target system the author drafts for. Content is filled in per target; a missing,
empty, or all-placeholder file is valid and reads as 'unconfigured' (the agent
then drafts from incident + experience alone). Never raises."""

from __future__ import annotations

import logging

import yaml

logger = logging.getLogger("intelliops.governance.system_context")


class SystemContextProvider:
    def __init__(self, path: str) -> None:
        self._path = path

    def load(self) -> dict | None:
        try:
            with open(self._path) as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            logger.info("system context unreadable at %s; treating as unconfigured", self._path)
            return None
        if not isinstance(data, dict):
            return None
        name = (data.get("system") or {}).get("name") or ""
        services = data.get("services") or []
        if not name and not services:
            return None  # all-placeholder
        return data

    def summarize(self) -> str:
        data = self.load()
        if data is None:
            return "System context: unconfigured (no target system described yet)."
        sys_ = data.get("system", {})
        lines = [f"System: {sys_.get('name', '?')} — {sys_.get('summary', '')}".strip()]
        for svc in data.get("services", []):
            deps = ", ".join(svc.get("depends_on", [])) or "none"
            mets = ", ".join(svc.get("key_metrics", [])) or "none"
            lines.append(f"- {svc.get('name', '?')}: {svc.get('role', '')}; depends on {deps}; metrics {mets}")
        actions = data.get("actions") or {}
        if actions:
            lines.append("Action notes: " + "; ".join(f"{k}={v}" for k, v in actions.items() if v))
        if data.get("notes"):
            lines.append(f"Notes: {data['notes']}")
        return "\n".join(lines)
```

- [ ] **Step 4: Create the placeholder config file**

`config/system_context.yaml` — the documented schema from spec §5.2, all values empty/`[]`, with comments explaining each field and that it is filled in when the target system exists. Confirm `yaml` (PyYAML) is already a dependency (it is used elsewhere; if not, add to the base deps — check `pyproject.toml` first).

- [ ] **Step 5: Add the settings field**

`common/config.py`: `system_context_path: str = "config/system_context.yaml"`.

- [ ] **Step 6: Run tests**

Run: `uv run pytest services/governance/tests/test_system_context.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add services/governance/adapters/system_context.py config/system_context.yaml common/config.py services/governance/tests/test_system_context.py
git commit -m "feat(governance): SystemContextProvider + placeholder system_context.yaml"
```

---

### Task 4: Author tools module (schemas + dispatch to stores) + tests

**Files:**
- Create: `services/governance/adapters/author_tools.py`
- Test: `services/governance/tests/test_author_tools.py`

**Interfaces:**
- Consumes: `SystemContextProvider`, `TrainingStore` (feedback), `AuditSink`, `AuthorDecisionStore`, the `Situation` in hand, and the closed action list.
- Produces:
  - `TOOL_SCHEMAS: list[dict]` — OpenAI `tools` array for the 6 tools (`get_system_context`, `get_incident_details`, `get_past_outcomes`, `get_human_decisions`, `get_past_decisions`, `list_available_actions`, `submit_runbook`). (7 entries — `submit_runbook` is a tool too.)
  - `AuthorToolbox` — bound to the request's context; `dispatch(name, arguments) -> dict` executes a read tool and returns a JSON-serializable result; each read tool is individually try/except'd so a store blip yields `{"error": "..."}` not a crash. `submit_runbook` is handled by the loop (Task 5), not dispatched here, but its schema lives here.
  - `ACTION_NOTES: dict[str,str]` — one-line usage note per closed action (static, curated; this is the only place action guidance is hardcoded, and it is generic SRE guidance, not target-specific).

- [ ] **Step 1: Write failing tests** (dispatch returns expected shapes; a store that raises yields an `error` dict, not an exception; `get_past_decisions` filters by signature; `list_available_actions` returns exactly the 7 closed actions with notes).

```python
# services/governance/tests/test_author_tools.py  (abbreviated — implementer expands)
from datetime import UTC, datetime
from common.contracts import AuthorDecision, Situation, SituationStatus, TrainingRecord, RemediationResult
from services.governance.adapters.author_tools import AuthorToolbox, TOOL_SCHEMAS, ACTION_NOTES
from services.governance.adapters.author_decision_store import InMemoryAuthorDecisionStore


class _Sig:  # minimal training store stub
    def __init__(self, recs): self._r = recs
    def read_all(self): return self._r


class _Audit:
    def __init__(self, recs): self._r = recs
    def records(self, cid): return [x for x in self._r if x.correlation_id == cid]
    def all(self): return self._r  # if the sink exposes this; else adapt


def _sit():
    now = datetime.now(UTC)
    return Situation(id="sit-1", status=SituationStatus.DIAGNOSED, severity="high",
                     first_seen=now, last_seen=now, signature="sig-x")


def test_schemas_cover_seven_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {"get_system_context", "get_incident_details", "get_past_outcomes",
                     "get_human_decisions", "get_past_decisions", "list_available_actions",
                     "submit_runbook"}


def test_list_actions_returns_closed_set():
    tb = AuthorToolbox(situation=_sit(), system_context=None, training_store=_Sig([]),
                       audit_sink=_Audit([]), decision_store=InMemoryAuthorDecisionStore())
    out = tb.dispatch("list_available_actions", {})
    assert set(out["actions"]) == set(ACTION_NOTES.keys())


def test_past_decisions_filtered_by_signature():
    ds = InMemoryAuthorDecisionStore()
    ds.record(AuthorDecision(signature="sig-x", proposal_id="p1", playbook_id="ai-sig-x-1",
                             actions=["restart"], cited_facts=[], ts=datetime.now(UTC),
                             disposition="accepted", outcome="worked"))
    tb = AuthorToolbox(situation=_sit(), system_context=None, training_store=_Sig([]),
                       audit_sink=_Audit([]), decision_store=ds)
    out = tb.dispatch("get_past_decisions", {"signature": "sig-x"})
    assert out["decisions"][0]["outcome"] == "worked"


def test_dispatch_catches_store_error():
    class _Boom:
        def read_all(self): raise RuntimeError("db down")
    tb = AuthorToolbox(situation=_sit(), system_context=None, training_store=_Boom(),
                       audit_sink=_Audit([]), decision_store=InMemoryAuthorDecisionStore())
    out = tb.dispatch("get_past_outcomes", {"signature": "sig-x"})
    assert "error" in out  # did not raise
```

- [ ] **Step 2: Run to verify fail.** `uv run pytest services/governance/tests/test_author_tools.py -v` → FAIL.

- [ ] **Step 3: Implement `author_tools.py`.**
  - `ACTION_NOTES` — the 7 closed actions with generic notes (e.g. `"restart": "recycle a wedged process / clear stuck in-memory state"`, `"scale": "add replicas for capacity contention"`, etc.). Keep the keys EXACTLY the Literal values.
  - `TOOL_SCHEMAS` — OpenAI function schemas; only `get_incident_details`/`get_past_outcomes`/`get_human_decisions`/`get_past_decisions` take a param (`situation_id` or `signature`); `submit_runbook` params: `playbook` (object), `rationale` (string), `cited_facts` (array of strings).
  - `AuthorToolbox.__init__(situation, system_context, training_store, audit_sink, decision_store)`.
  - `dispatch(name, arguments)`:
    - `get_system_context` → `{"context": system_context.summarize() if system_context else "unconfigured"}`
    - `get_incident_details` → serialize the in-hand `Situation` (metrics/members/signature/severity); ignore the arg's id (we serve the situation we were given — never look up an arbitrary id).
    - `get_past_outcomes` → compute per-playbook worked/total for `signature` from `training_store.read_all()` filtered by signature (reuse `services/feedback/metrics.py::compute_metrics`-style aggregation, or import it) + last N records.
    - `get_human_decisions` → from `audit_sink`, the propose/approve-proposal/reject records whose proposal relates to this signature. (Audit records key on `correlation_id`; proposals set `correlation_id` to `situation.id`/`proposal_id`. If audit has no "list all" method, add a minimal read or filter what's queryable — implementer checks the `AuditSink` protocol and adapts; do NOT add a broad new audit API if a scoped one suffices.)
    - `get_past_decisions` → `decision_store.by_signature(signature)`, serialized; **truncate/label any `note`** as untrusted prior reasoning.
    - `list_available_actions` → `{"actions": {name: note}}`.
    - Each branch wrapped so an exception → `{"error": "<class name>"}` and is logged.

- [ ] **Step 4: Run tests.** Expected PASS.

- [ ] **Step 5: Commit.**
```bash
git add services/governance/adapters/author_tools.py services/governance/tests/test_author_tools.py
git commit -m "feat(governance): author tool schemas + toolbox dispatch to first-party stores"
```

---

### Task 5: `RunbookAuthorAgent` — the tool-calling loop (carries #48 429/backoff/token-cap) + tests

**Files:**
- Modify: `services/governance/adapters/runbook_author.py` (add `RunbookAuthorAgent`; keep `NullRunbookAuthor` and `OpenAICompatibleRunbookAuthor` — see Task 7 for selection)
- Test: `services/governance/tests/test_runbook_author_agent.py`

**Interfaces:**
- Consumes: an OpenAI-compatible chat endpoint with `tools`; an `AuthorToolbox` factory (per-draft, bound to the situation + stores); the reused 429/`_parse_retry_after`/backoff/`max_tokens` machinery from `OpenAICompatibleRunbookAuthor`.
- Produces: `RunbookAuthorAgent(base_url, model, api_key, toolbox_factory, timeout_seconds=..., max_attempts=..., max_rounds=6)` with `draft(situation, hint) -> tuple[Playbook, str, list[str]] | None` returning `(playbook, rationale, cited_facts)`. (Note the added `cited_facts` in the return — Task 6 consumes it.)

- [ ] **Step 1: Write failing tests** using a fake chat client that returns scripted `tool_calls` then a final `submit_runbook` call. Cover:
  - happy path: model calls `get_past_decisions` then `submit_runbook` → returns `(Playbook, rationale, cited_facts)`; the toolbox saw the `get_past_decisions` call.
  - budget: model never submits (keeps calling read tools) → after `max_rounds` returns `None`.
  - invalid submit: `submit_runbook` payload with an out-of-catalog action → `None` (validation gate).
  - id omitted in submit → still validates (carry the Task from #47: inject placeholder id before validate).
  - 429 on a round → honors backoff then continues (reuse #48 tests' `monkeypatch` of `time.sleep`).
  - a read-tool `{"error": ...}` result does NOT crash the loop — model can still submit.

```python
# sketch of the fake client contract
class _FakeChat:
    """Returns queued responses. Each response is either
    {"tool_calls": [{"id","function":{"name","arguments"}}]} or
    {"content": ...} shaped like OpenAI; the agent feeds tool results back."""
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `RunbookAuthorAgent`.**
  - Build initial messages: system prompt (role + STRICT rules + that it MUST end by calling `submit_runbook`; carry the #48 prompt hardening: concrete integers, no id) + a user message naming the incident + `hint`.
  - Loop up to `max_rounds`:
    - POST `chat/completions` with `messages`, `tools=TOOL_SCHEMAS`, `tool_choice="auto"`, `max_tokens=_MAX_COMPLETION_TOKENS`. Reuse the exact 429/transport/non-200 handling + `_parse_retry_after` + backoff from `OpenAICompatibleRunbookAuthor` (refactor the shared HTTP-with-retry into a small helper both use, rather than duplicating).
    - If the response has `tool_calls`:
      - for each call: if `submit_runbook` → validate its `playbook` (inject placeholder id if missing; `Playbook.model_validate`) → on success return `(playbook, rationale, cited_facts)`; on validation failure, append a tool result saying "invalid: <reason>, fix and resubmit" and continue (one corrective round is the retry — bounded by `max_rounds`).
      - else → `toolbox.dispatch(name, args)`, append the result as a `role:"tool"` message (with `tool_call_id`).
    - If the response has plain content and no tool call → nudge once ("call submit_runbook to finish"); if it still doesn't, the budget ends the loop → `None`.
  - Any exception anywhere → log → `None`.

- [ ] **Step 4: Run tests → PASS.**

- [ ] **Step 5: Commit.**
```bash
git add services/governance/adapters/runbook_author.py services/governance/tests/test_runbook_author_agent.py
git commit -m "feat(governance): tool-calling RunbookAuthorAgent (bounded loop, reuses 429 backoff)"
```

---

### Task 6: Wire the agent into governance — construct it, record decisions on draft, update disposition on approve/reject + tests

**Files:**
- Modify: `services/governance/app.py` (`_make_runbook_author`, `_init_state`, `propose_playbook`, `approve_proposed`, `reject_proposed`)
- Test: `services/governance/tests/test_proposed_routes.py` (extend)

**Interfaces:**
- Consumes: `Stores.training_store`, `Stores.author_decision_store`, `SystemContextProvider`, the agent from Task 5, the toolbox from Task 4.
- Produces: on a successful draft, an `AuthorDecision` recorded (best-effort); on approve/reject, `update_disposition`. `propose_playbook` still returns `ProposedPlaybook` (contract unchanged).

- [ ] **Step 1: Write failing tests** (extend `test_proposed_routes.py`, using an in-memory decision store + a fake author that returns a fixed `(playbook, rationale, cited_facts)`):
  - after `POST /playbooks/proposed`, the decision store has a `pending` record with the proposal's `playbook_id`, the chosen actions, and the cited facts.
  - after `approve`, that record's disposition is `accepted` (+ `decided_by`).
  - after `reject`, disposition is `rejected`.
  - a decision-store write that raises does NOT fail the proposal (best-effort) — proposal still returns 200.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement.**
  - `_init_state`: `app.state.training_store = stores.training_store`; `app.state.author_decision_store = stores.author_decision_store`; build a `SystemContextProvider(settings.system_context_path)`.
  - `_make_runbook_author`: when `runbook_author_mode == "openai" and llm_runbook_endpoint`, construct `RunbookAuthorAgent` with a `toolbox_factory = lambda situation: AuthorToolbox(situation, system_context, stores.training_store, stores.audit_sink, stores.author_decision_store)`. Else `NullRunbookAuthor()`. (The agent replaces the single-shot `OpenAICompatibleRunbookAuthor` as the live impl; keep the class available but the agent is what governance constructs. See Task 7 note.)
  - `propose_playbook`: `draft()` now returns `(playbook, rationale, cited_facts)` (agent) — but `NullRunbookAuthor.draft` returns `None`, and back-compat: if a 2-tuple is returned treat `cited_facts=[]`. After building `normalized` (with the `ai-<sig>-<uuid>` id), best-effort `app.state.author_decision_store.record(AuthorDecision(signature=situation.signature, proposal_id=proposal.id, playbook_id=normalized.id, actions=[s.action for s in normalized.steps], cited_facts=cited_facts, note=None, disposition="pending", outcome="unknown", ts=now))` wrapped in try/except (log on failure).
  - `approve_proposed` / `reject_proposed`: after the existing status set + audit write, best-effort `app.state.author_decision_store.update_disposition(proposal_id, "accepted"|"rejected", body.decided_by)`.

- [ ] **Step 4: Run tests → PASS.** Also `uv run pytest services/governance/tests/ -q` (full governance suite green).

- [ ] **Step 5: Commit.**
```bash
git add services/governance/app.py services/governance/tests/test_proposed_routes.py
git commit -m "feat(governance): construct agent, record decisions on draft, update disposition on approve/reject"
```

---

### Task 7: Governance consumer of `remediation.outcomes` → `update_outcome` (close the loop) + tests

**Files:**
- Create: `services/governance/consumer.py`
- Modify: `services/governance/app.py` (lifespan starts the consumer thread)
- Test: `services/governance/tests/test_governance_consumer.py`

**Interfaces:**
- Consumes: the bus (`iter_models(bus, "remediation.outcomes", "governance", RemediationOutcome)`), `AuthorDecisionStore`. Uses consumer group `"governance"` (distinct from feedback's `"feedback"` group — both durably receive every outcome).
- Produces: for each outcome, `decision_store.update_outcome(outcome.playbook_id, "worked" if result==SUCCESS else "failed", outcome.health_after)`. Runs in a daemon thread started in the lifespan (mirror `services/feedback/app.py`).

- [ ] **Step 1: Write failing test** — feed a fake bus a `RemediationOutcome` for a known `playbook_id`; assert the matching decision's `outcome` flips to `worked`/`failed`; an outcome for an unknown playbook_id is a no-op (no raise).

```python
# uses the same iter_models fake pattern as feedback's consumer tests
def test_consumer_updates_decision_outcome():
    ds = InMemoryAuthorDecisionStore()
    ds.record(_decision(playbook_id="ai-sig-x-1", disposition="accepted"))
    outcomes = [RemediationOutcome(situation_id="sit-1", playbook_id="ai-sig-x-1",
                result=RemediationResult.SUCCESS, health_after="healthy", ts=datetime.now(UTC))]
    run_consumer(_FakeBus(outcomes), ds, stop_event=_immediate_stop_after(1))
    assert ds.by_signature("sig-x")[0].outcome == "worked"
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `run_consumer(bus, decision_store, stop_event)`** mirroring `services/feedback/consumer.py` (loop `iter_models`, break on stop_event, best-effort `update_outcome`, catch per-iteration exceptions so the thread never dies — carry the bus-resilience lesson).

- [ ] **Step 4: Start it in the governance lifespan** — governance currently has no consumer thread; add one exactly like feedback's (`threading.Event`, daemon `Thread`, `app.state.consumer_stop`, stop on shutdown). Guard: only start if `bus` is available on `app.state` (it is — governance already makes a bus? verify; if governance has no bus today, add `make_bus(settings)` in `_init_state`, matching feedback).

- [ ] **Step 5: Run tests → PASS.** Full governance suite green: `uv run pytest services/governance/tests/ -q`.

- [ ] **Step 6: Commit.**
```bash
git add services/governance/consumer.py services/governance/app.py services/governance/tests/test_governance_consumer.py
git commit -m "feat(governance): consume remediation.outcomes to close the author decision loop"
```

---

### Task 8: Chart wiring — mount `system_context.yaml`, keep off-by-default; docs

**Files:**
- Create: `deploy/k8s/platform/templates/system-context-configmap.yaml` (a ConfigMap from `config/system_context.yaml`)
- Modify: `deploy/k8s/platform/templates/service-deployment.yaml` (mount it into governance at the `system_context_path`) OR `configmap.yaml` env (`INTELLIOPS_SYSTEM_CONTEXT_PATH`)
- Modify: `deploy/k8s/platform/values.yaml` + `values-live.yaml` (path; nothing secret)
- Modify: `README.md` / a short doc note on the new feature + how to fill in `system_context.yaml`
- Test: `helm lint` + `helm template` render checks (manual, documented in the task)

**Interfaces:**
- Consumes: `settings.system_context_path`.
- Produces: governance pod reads the mounted context; safe-by-default unchanged (empty placeholder → "unconfigured", agent still works).

- [ ] **Step 1:** Add `INTELLIOPS_SYSTEM_CONTEXT_PATH` to the configmap (defaulting to the mount path) and a ConfigMap carrying the placeholder file content (or mount the repo file via a volume — implementer picks the simpler of: (a) bake `config/system_context.yaml` into the image and just set the path, or (b) a ConfigMap + volumeMount. Baking is simplest and matches how other config travels; prefer (a) unless operators need to edit without a rebuild, in which case (b)).
- [ ] **Step 2:** `helm lint deploy/k8s/platform` (dockerized helm) → 0 failed.
- [ ] **Step 3:** `helm template ... -f values-live.yaml` → confirm governance gets the path env and (if (b)) the mount; confirm NO new secret and off-by-default retained.
- [ ] **Step 4:** README note: what the feature is, that it's off-by-default, and the `system_context.yaml` schema to fill in per target.
- [ ] **Step 5: Commit.**
```bash
git add deploy/k8s/platform README.md config/system_context.yaml
git commit -m "feat(deploy): wire system_context for the agent; docs; safe-by-default unchanged"
```

---

## Notes for the executor

- **Task 7 bus dependency:** verify whether governance already constructs a bus. If not, adding `make_bus` in `_init_state` is in-scope for Task 7 (feedback is the reference). If governance is intentionally bus-free today, that is the one place to pause and confirm before adding a consumer — but the spec's outcome-linkage (open Q4) chose governance-consumes, so this is the sanctioned path.
- **Do not modify** `RemediationStep`, the denylist, sandbox, RCA ranking, correlation, or the execution path.
- **`OpenAICompatibleRunbookAuthor`** (single-shot) may be retained as dead-simple fallback code or removed; the plan keeps it importable but governance constructs the agent. If the whole-branch reviewer flags it as dead code, removing it is acceptable (its tests then move/adapt to the agent).
- **Slim-boundary:** after Task 5/6, `uv run python -c "import services.governance.app"` must not pull ML deps (agent uses httpx + stdlib only). Verify in Task 6.
