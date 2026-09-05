# AI-authored runbooks (propose → approve) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the AI draft a *typed* runbook for an incident that has no matching playbook, store it as a proposal, and require a human (RBAC-gated) to approve it before the inner Playbook enters the live registry — the AI proposes, a human disposes, and the closed `RemediationStep` Literal guarantees no unsafe action can be smuggled in.

**Architecture:** A new `ProposedPlaybook` contract wraps a typed `Playbook` + proposal metadata. A `RunbookAuthor` adapter (parallel to RCA's `ExplanationProvider`: `NullRunbookAuthor` default + `OpenAICompatibleRunbookAuthor` LLM variant, fail-to-nothing, never raises) drafts it. Five governance routes (`/playbooks/proposed` [POST/GET], `/{id}` [GET], `/{id}/approve`, `/{id}/reject`) mirror the existing `decide_approval`/`graduate_playbook` shape (get-404 → RBAC-403 → mutate → audit), reusing the existing `approve`/`reject` RBAC actions. Approve calls `playbook_store.register(inner)`. All human-initiated on-demand; no LLM in any consumer hot path; no execution-path change.

**Tech Stack:** Python 3.11/3.12, Pydantic v2, FastAPI, `httpx` (sync), pytest + FastAPI TestClient, React/TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-09-04-ai-authored-runbooks-design.md` (read alongside this plan). Companion: `docs/sandbox-and-ai-runbooks-design-note.md`. This is PR C (final) of the arc; PRs A (#33 sandbox) + B (#34 tier-2 vocab) are merged to master.

## Global Constraints

- **Branch `feat/ai-authored-runbooks` off current master** (both predecessors merged). This relies on the **tier-2** `RemediationStep` Literal (7 actions) from #34 — an AI draft's steps validate against the widened closed set. Confirm `common/contracts.py`'s `RemediationStep.action` Literal includes `patch_resource_limits`/`rollback_to_revision`/`patch_probe` before starting; if it only has the original 4, #34 isn't on your base — STOP and report.
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~463 base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean.
- **Env:** `uv sync --extra ml --extra k8s` once (a bare `uv sync` fails collection — missing `river`/`kubernetes`).
- **Safety invariants (must hold at every task):** an AI-proposed playbook's `steps` validate against the closed `RemediationStep` Literal (unsafe/malformed action → parse fails → NO proposal, author returns `None`); a stored proposal's `playbook.hitl_mode == HITL` always (forced, never AUTO/DISABLED); the proposal + inner playbook `id` are SERVER-assigned; the ONLY path to the live registry is the RBAC-gated approve route; approve/reject are audited.
- **Fail-to-nothing:** `RunbookAuthor` NEVER raises — every failure path returns `None` (mirror `OpenAICompatibleExplanationProvider`). `NullRunbookAuthor.draft` always returns `None`.
- **Test-safe default:** `runbook_author_mode` defaults `"off"` → `NullRunbookAuthor`; the base suite + CI never hit an endpoint; the 463 base stays green.
- **No RBAC change:** reuse the existing `approve`/`reject` actions on `playbook:*`. Do NOT edit `policies/rbac_policy.yaml`.
- **No execution-path change:** do NOT touch `services/action/remediate.py`. An approved playbook enters the same `PlaybookStore.register` and is thereafter subject to every existing gate (denylist + sandbox + HITL).
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** push; open a PR against master; the USER merges. Never merge to master.

---

## File Structure

- `common/contracts.py` — add `ProposedPlaybookStatus` enum + `ProposedPlaybook` model.
- `common/interfaces.py` — add the `RunbookAuthor` protocol.
- `common/config.py` — add `runbook_author_mode` + `llm_runbook_*` fields (mirror `llm_explanation_*`).
- `services/governance/adapters/runbook_author.py` (new) — `NullRunbookAuthor` + `OpenAICompatibleRunbookAuthor`.
- `services/governance/adapters/proposed_store.py` (new) — `InMemoryProposedPlaybookStore`.
- `services/governance/app.py` — `_make_runbook_author` factory; `app.state.proposed_store` + `app.state.runbook_author` wiring in `_init_state`; the 5 routes + a reset route.
- `frontend/src/data/types.ts` — `ProposedPlaybook` type + status.
- A frontend proposals view/panel + a "Draft a runbook with AI" affordance on a gap incident + a mock proposal.
- `deploy/k8s/README.md` (or a governance README section) — the propose→approve flow + safety guarantees.
- Tests (flat `tests/` + `services/governance/tests/` both exist):
  - `tests/test_ai_runbook_contracts.py` (new) — contract validation.
  - `services/governance/tests/test_runbook_author.py` (new) — the adapter (null + LLM fail paths + unsafe-action rejection).
  - `services/governance/tests/test_proposed_store.py` (new) — the store.
  - `services/governance/tests/test_proposed_routes.py` (new) — the 5 routes (mirror `test_governance_api.py` / `test_decide_rbac.py`).

---

## Task 1: Contracts + interface + config

**Files:**
- Modify: `common/contracts.py` (add `ProposedPlaybookStatus` + `ProposedPlaybook` after the `Playbook` class)
- Modify: `common/interfaces.py` (add `RunbookAuthor` protocol)
- Modify: `common/config.py` (add author config fields after the `llm_explanation_*` block at ~:69-72)
- Test: `tests/test_ai_runbook_contracts.py` (new)

**Interfaces:**
- Produces:
  - `common.contracts.ProposedPlaybookStatus` enum: `PROPOSED="proposed"`, `APPROVED="approved"`, `REJECTED="rejected"`.
  - `common.contracts.ProposedPlaybook(id: str, playbook: Playbook, status: ProposedPlaybookStatus = PROPOSED, proposed_by: str, rationale: str | None = None, source_situation_id: str | None = None, decided_by: str | None = None, ts: datetime)`.
  - `common.interfaces.RunbookAuthor` protocol: `draft(self, situation: Situation, hint: str | None = None) -> tuple[Playbook, str | None] | None`.
  - `Settings.runbook_author_mode: str = "off"` + `llm_runbook_endpoint`/`llm_runbook_model`/`llm_runbook_timeout_seconds`/`llm_runbook_api_key`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ai_runbook_contracts.py`:

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.contracts import (
    HitlMode,
    Playbook,
    ProposedPlaybook,
    ProposedPlaybookStatus,
    RemediationStep,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _playbook(action="restart"):
    return Playbook(
        id="pb-1", name="n", match_rule="*",
        steps=[RemediationStep(action=action)], hitl_mode=HitlMode.HITL, reversible=True,
    )


def test_proposed_playbook_validates_and_defaults_status():
    p = ProposedPlaybook(id="prop-1", playbook=_playbook(), proposed_by="runbook-author", ts=NOW)
    assert p.status == ProposedPlaybookStatus.PROPOSED
    assert p.playbook.steps[0].action == "restart"
    assert p.rationale is None and p.decided_by is None


def test_inner_playbook_with_unsafe_action_cannot_be_constructed():
    # The load-bearing guarantee: an out-of-set action fails at the inner Playbook.
    with pytest.raises(ValidationError):
        ProposedPlaybook(
            id="prop-2",
            playbook={"id": "x", "name": "n", "match_rule": "*",
                      "steps": [{"action": "delete"}], "hitl_mode": "hitl"},
            proposed_by="runbook-author", ts=NOW,
        )


def test_tier2_action_is_accepted_in_a_proposal():
    p = ProposedPlaybook(id="prop-3", playbook=_playbook(action="patch_probe"),
                         proposed_by="runbook-author", ts=NOW)
    assert p.playbook.steps[0].action == "patch_probe"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ai_runbook_contracts.py -v`
Expected: FAIL — `ProposedPlaybook`/`ProposedPlaybookStatus` don't exist (ImportError).

- [ ] **Step 3: Add the contracts**

In `common/contracts.py`, after the `Playbook` class, add:

```python
class ProposedPlaybookStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProposedPlaybook(BaseModel):
    id: str  # server-assigned
    playbook: Playbook  # the typed draft — steps validate via the closed Literal
    status: ProposedPlaybookStatus = ProposedPlaybookStatus.PROPOSED
    proposed_by: str
    rationale: str | None = None
    source_situation_id: str | None = None
    decided_by: str | None = None
    ts: datetime
```

`ProposedPlaybook` is defined AFTER `Playbook`, so the nested type resolves with no forward-ref.

- [ ] **Step 4: Add the `RunbookAuthor` protocol**

In `common/interfaces.py`, add `Situation`/`Playbook` to the `common.contracts` import if not present, then add (matching the file's `@runtime_checkable`/`Protocol` style):

```python
@runtime_checkable
class RunbookAuthor(Protocol):
    """Drafts a typed Playbook for a gap. Returns None when it cannot (fail-to-
    nothing) — never raises. The caller forces hitl_mode=HITL and a server id."""

    def draft(
        self, situation: Situation, hint: str | None = None
    ) -> tuple[Playbook, str | None] | None: ...
```

- [ ] **Step 5: Add the config fields**

In `common/config.py`, after the `llm_explanation_*` block (~:69-72), add:

```python
    runbook_author_mode: str = "off"  # "off" | "openai"
    llm_runbook_endpoint: str = ""  # empty = NullRunbookAuthor, no network
    llm_runbook_model: str = "gpt-4o-mini"
    llm_runbook_timeout_seconds: float = 10.0
    llm_runbook_api_key: str = ""
```

- [ ] **Step 6: Run tests + full suite + lint**

Run: `uv run pytest tests/test_ai_runbook_contracts.py -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: the 3 new pass; base suite green (nothing uses the new types yet); lint clean.

- [ ] **Step 7: Commit**

```bash
git add common/contracts.py common/interfaces.py common/config.py tests/test_ai_runbook_contracts.py
git commit -m "feat(runbooks): ProposedPlaybook contract, RunbookAuthor protocol, author config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: The RunbookAuthor adapters

**Files:**
- Create: `services/governance/adapters/runbook_author.py`
- Test: `services/governance/tests/test_runbook_author.py` (new)

**Interfaces:**
- Consumes: `Playbook`, `Situation`, `RunbookAuthor` (Task 1).
- Produces: `NullRunbookAuthor` (`draft(...) -> None`) and `OpenAICompatibleRunbookAuthor(base_url, model, api_key="", timeout_seconds=10.0, http_client=None)` implementing `draft(situation, hint=None) -> tuple[Playbook, str | None] | None`.

**Read first:** `services/rca/adapters/explanation_provider.py` — `OpenAICompatibleRunbookAuthor` mirrors `OpenAICompatibleExplanationProvider`'s structure and fallback discipline (sync `httpx.Client`; on `httpx.HTTPError`/non-200/non-JSON/missing content → fallback; here fallback = return `None`). The ONE difference: instead of returning text, it parses the model's content as JSON and runs `Playbook.model_validate(parsed)` inside `try/except ValidationError` → `None` on failure (this is where an unsafe action is rejected).

- [ ] **Step 1: Write the failing tests**

Create `services/governance/tests/test_runbook_author.py`. Use a fake httpx client so no network is touched — model it on how `explanation_provider` tests stub the client (a fake with a `.post(...)` returning a fake response with `.status_code` + `.json()`). If no such fake exists in the repo, define one inline:

```python
import json

import httpx
import pytest

from common.contracts import Situation, SituationStatus
from services.governance.adapters.runbook_author import (
    NullRunbookAuthor,
    OpenAICompatibleRunbookAuthor,
)
from datetime import UTC, datetime


def _situation():
    now = datetime.now(UTC)
    return Situation(id="sit-1", status=SituationStatus.DIAGNOSED, severity="high",
                     first_seen=now, last_seen=now, signature="sig-1")


class _FakeResp:
    def __init__(self, status_code=200, body=None, raise_json=False):
        self.status_code = status_code
        self._body = body
        self._raise_json = raise_json
    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    def __init__(self, resp=None, raise_http=False):
        self._resp = resp
        self._raise_http = raise_http
    def post(self, *a, **k):
        if self._raise_http:
            raise httpx.ConnectError("unreachable")
        return self._resp


def _content(playbook_json: dict, rationale="because") -> dict:
    # an OpenAI-chat-shaped body whose message content is the JSON draft
    inner = {"playbook": playbook_json, "rationale": rationale}
    return {"choices": [{"message": {"content": json.dumps(inner)}}]}


_VALID_DRAFT = {
    "id": "ignored-by-server", "name": "Drafted restart", "match_rule": "*",
    "steps": [{"action": "restart"}], "hitl_mode": "hitl", "reversible": True,
}


def test_null_author_returns_none():
    assert NullRunbookAuthor().draft(_situation()) is None


def test_valid_draft_returns_typed_playbook():
    client = _FakeClient(_FakeResp(200, _content(_VALID_DRAFT)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    result = author.draft(_situation())
    assert result is not None
    playbook, rationale = result
    assert playbook.steps[0].action == "restart"
    assert rationale == "because"


def test_unsafe_action_in_draft_returns_none():
    bad = {**_VALID_DRAFT, "steps": [{"action": "delete"}]}
    client = _FakeClient(_FakeResp(200, _content(bad)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    assert author.draft(_situation()) is None  # model_validate rejects "delete"


@pytest.mark.parametrize("resp,raise_http", [
    (None, True),                                   # transport error
    (_FakeResp(500, {}), False),                    # non-200
    (_FakeResp(200, None, raise_json=True), False), # non-JSON
    (_FakeResp(200, {"choices": []}), False),       # missing content
    (_FakeResp(200, {"choices": [{"message": {"content": "not json at all"}}]}), False),  # content not JSON
])
def test_failure_paths_return_none_never_raise(resp, raise_http):
    client = _FakeClient(resp, raise_http=raise_http)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    assert author.draft(_situation()) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/governance/tests/test_runbook_author.py -v`
Expected: FAIL — the adapter module doesn't exist.

- [ ] **Step 3: Implement the adapters**

Create `services/governance/adapters/runbook_author.py`. Mirror `explanation_provider.py`'s import style + fallback discipline. The system prompt enumerates the 7 allowed actions and instructs strict JSON `{"playbook": {...}, "rationale": "..."}`; the type system (not the prompt) is the guarantee.

```python
"""RunbookAuthor implementations: draft a typed Playbook for a gap.

NullRunbookAuthor is the CI-safe default — no network, always None.
OpenAICompatibleRunbookAuthor talks to any OpenAI-chat-completions-shaped
endpoint via a synchronous httpx.Client and parses the model's content into a
typed Playbook. It NEVER raises: any failure — transport, non-200, non-JSON,
missing content, content that isn't JSON, or a Playbook that fails validation
(e.g. an out-of-set action) — returns None (no draft). The closed
RemediationStep Literal is what actually rejects unsafe actions; the prompt
only asks nicely."""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import ValidationError

from common.contracts import Playbook, Situation

logger = logging.getLogger("intelliops.governance.runbook_author")

_ALLOWED = "restart, scale, rollback_deploy, wait, patch_resource_limits, rollback_to_revision, patch_probe"


class NullRunbookAuthor:
    def draft(self, situation: Situation, hint: str | None = None):
        return None


class OpenAICompatibleRunbookAuthor:
    def __init__(self, base_url, model, api_key="", timeout_seconds=10.0, http_client=None):
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def draft(self, situation: Situation, hint: str | None = None):
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": (
                    "You are an SRE assistant that writes Kubernetes remediation runbooks. "
                    "Respond with STRICT JSON only, shaped as "
                    '{"playbook": {"name": str, "match_rule": str, "steps": [{"action": str, ...}], '
                    '"hitl_mode": "hitl", "reversible": bool, "rollback_steps": [...]}, "rationale": str}. '
                    f"Each step action MUST be one of: {_ALLOWED}. Any other action is rejected."
                )},
                {"role": "user", "content": (
                    f"Incident {situation.id} (severity {situation.severity}, signature "
                    f"{situation.signature}) has no matching runbook. Draft one. Hint: {hint or 'none'}."
                )},
            ],
        }
        try:
            resp = self._client.post(f"{self._base}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            logger.info("runbook author endpoint unreachable (%s); no draft", exc.__class__.__name__)
            return None
        if resp.status_code != 200:
            logger.info("runbook author endpoint status %s; no draft", resp.status_code)
            return None
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.info("runbook author response missing/invalid content (%s); no draft", exc.__class__.__name__)
            return None
        if not content:
            return None
        try:
            parsed = json.loads(content)
            playbook = Playbook.model_validate(parsed["playbook"])
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            logger.info("runbook author draft did not validate (%s); no draft", exc.__class__.__name__)
            return None
        rationale = parsed.get("rationale") if isinstance(parsed, dict) else None
        return playbook, rationale
```

- [ ] **Step 4: Run the adapter tests**

Run: `uv run pytest services/governance/tests/test_runbook_author.py -v`
Expected: PASS (all, incl. the parametrized failure paths + the unsafe-action rejection).

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add services/governance/adapters/runbook_author.py services/governance/tests/test_runbook_author.py
git commit -m "feat(runbooks): RunbookAuthor adapters — null default + OpenAI, fail-to-nothing

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: The ProposedPlaybookStore

**Files:**
- Create: `services/governance/adapters/proposed_store.py`
- Test: `services/governance/tests/test_proposed_store.py` (new)

**Interfaces:**
- Consumes: `ProposedPlaybook`, `ProposedPlaybookStatus` (Task 1).
- Produces: `InMemoryProposedPlaybookStore` with `add(proposal)`, `get(id) -> ProposedPlaybook | None`, `list(status=None) -> list[ProposedPlaybook]`, `set_status(id, status, decided_by) -> ProposedPlaybook | None`, and a `clear()` for reset. Mirror `InMemoryApprovalStore` (`_by_id` dict).

- [ ] **Step 1: Write the failing test**

Create `services/governance/tests/test_proposed_store.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    HitlMode, Playbook, ProposedPlaybook, ProposedPlaybookStatus, RemediationStep,
)
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _prop(pid="prop-1", status=ProposedPlaybookStatus.PROPOSED):
    pb = Playbook(id="pb", name="n", match_rule="*",
                  steps=[RemediationStep(action="restart")], hitl_mode=HitlMode.HITL)
    return ProposedPlaybook(id=pid, playbook=pb, status=status, proposed_by="runbook-author", ts=NOW)


def test_add_get_list_set_status():
    s = InMemoryProposedPlaybookStore()
    s.add(_prop("prop-1"))
    s.add(_prop("prop-2"))
    assert s.get("prop-1").id == "prop-1"
    assert s.get("missing") is None
    assert len(s.list()) == 2
    updated = s.set_status("prop-1", ProposedPlaybookStatus.APPROVED, "oncall-alice")
    assert updated.status == ProposedPlaybookStatus.APPROVED
    assert updated.decided_by == "oncall-alice"
    assert len(s.list(status=ProposedPlaybookStatus.PROPOSED)) == 1  # only prop-2 remains proposed
    assert s.set_status("missing", ProposedPlaybookStatus.REJECTED, "x") is None


def test_clear():
    s = InMemoryProposedPlaybookStore()
    s.add(_prop())
    s.clear()
    assert s.list() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/governance/tests/test_proposed_store.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the store**

Create `services/governance/adapters/proposed_store.py`:

```python
from __future__ import annotations

from common.contracts import ProposedPlaybook, ProposedPlaybookStatus


class InMemoryProposedPlaybookStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ProposedPlaybook] = {}

    def add(self, proposal: ProposedPlaybook) -> None:
        self._by_id[proposal.id] = proposal

    def get(self, proposal_id: str) -> ProposedPlaybook | None:
        return self._by_id.get(proposal_id)

    def list(self, status: ProposedPlaybookStatus | None = None) -> list[ProposedPlaybook]:
        items = list(self._by_id.values())
        if status is not None:
            items = [p for p in items if p.status == status]
        return items

    def set_status(
        self, proposal_id: str, status: ProposedPlaybookStatus, decided_by: str
    ) -> ProposedPlaybook | None:
        cur = self._by_id.get(proposal_id)
        if cur is None:
            return None
        updated = cur.model_copy(update={"status": status, "decided_by": decided_by})
        self._by_id[proposal_id] = updated
        return updated

    def clear(self) -> None:
        self._by_id.clear()
```

- [ ] **Step 4: Run + full suite + lint**

Run: `uv run pytest services/governance/tests/test_proposed_store.py -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add services/governance/adapters/proposed_store.py services/governance/tests/test_proposed_store.py
git commit -m "feat(runbooks): InMemoryProposedPlaybookStore

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Governance routes for the propose → approve lifecycle

**Files:**
- Modify: `services/governance/app.py` (add `_make_runbook_author`; wire `app.state.proposed_store` + `app.state.runbook_author` in `_init_state`; add 5 routes + a reset)
- Test: `services/governance/tests/test_proposed_routes.py` (new)

**Interfaces:**
- Consumes: `NullRunbookAuthor`/`OpenAICompatibleRunbookAuthor` (Task 2), `InMemoryProposedPlaybookStore` (Task 3), `ProposedPlaybook`/`ProposedPlaybookStatus` (Task 1), the existing `app.state.rbac`/`playbook_store`/`audit_sink`.
- Produces routes: `POST /playbooks/proposed`, `GET /playbooks/proposed`, `GET /playbooks/proposed/{id}`, `POST /playbooks/proposed/{id}/approve`, `POST /playbooks/proposed/{id}/reject`, `POST /reset-proposed`.

**Mirror the exact shape** of `decide_approval`/`graduate_playbook` (`app.py:121-165`): get-then-404 → `app.state.rbac.check(actor, action, resource)`-or-403 → mutate → `app.state.audit_sink.write(AuditRecord(...))` → return.

- [ ] **Step 1: Write the failing tests**

Create `services/governance/tests/test_proposed_routes.py`, modeled on `test_governance_api.py` / `test_decide_rbac.py`. The `_client()` sets `app.state` with in-memory stores + an inline RBAC + a **stub author**, and returns a `TestClient`.

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook, RemediationStep, Situation, SituationStatus
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.adapters.proposed_store import InMemoryProposedPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _situation_json():
    return Situation(id="sit-1", status=SituationStatus.DIAGNOSED, severity="high",
                     first_seen=NOW, last_seen=NOW, signature="sig-1").model_dump(mode="json")


class _StubAuthor:
    def __init__(self, result):  # result is (Playbook, rationale) or None
        self._result = result
    def draft(self, situation, hint=None):
        return self._result


def _client(author):
    from services.governance.app import app
    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.rbac = RbacPolicy(
        roles={"approver": [
            {"action": "approve", "resource": "playbook:*"},
            {"action": "reject", "resource": "playbook:*"},
        ]},
        actors={"oncall-alice": ["approver"], "random-bob": []},
    )
    app.state.runbook_author = author
    return TestClient(app)


def _draft_playbook(action="restart", hitl=HitlMode.AUTO):
    # note hitl=AUTO here to prove the route FORCES hitl to HITL
    return Playbook(id="ai-supplied-id", name="drafted", match_rule="*",
                    steps=[RemediationStep(action=action)], hitl_mode=hitl, reversible=True)


def test_propose_stores_proposal_not_registry():
    c = _client(_StubAuthor((_draft_playbook(), "because cpu")))
    resp = c.post("/playbooks/proposed",
                  json={"situation": _situation_json(), "requested_by": "oncall-alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "proposed"
    assert body["playbook"]["hitl_mode"] == "hitl"          # FORCED
    assert body["playbook"]["id"] != "ai-supplied-id"       # server-assigned
    assert body["source_situation_id"] == "sit-1"
    # not in the live registry yet
    assert c.get("/playbooks").json() == [] or all(p["id"] != body["playbook"]["id"] for p in c.get("/playbooks").json())


def test_propose_none_author_returns_422_stores_nothing():
    c = _client(_StubAuthor(None))
    resp = c.post("/playbooks/proposed",
                  json={"situation": _situation_json(), "requested_by": "oncall-alice"})
    assert resp.status_code == 422
    assert c.get("/playbooks/proposed").json() == []


def test_propose_forbidden_for_actor_without_permission():
    c = _client(_StubAuthor((_draft_playbook(), None)))
    resp = c.post("/playbooks/proposed",
                  json={"situation": _situation_json(), "requested_by": "random-bob"})
    assert resp.status_code == 403


def test_approve_registers_into_live_registry():
    c = _client(_StubAuthor((_draft_playbook(), "r")))
    pid = c.post("/playbooks/proposed",
                 json={"situation": _situation_json(), "requested_by": "oncall-alice"}).json()["id"]
    resp = c.post(f"/playbooks/proposed/{pid}/approve", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    # now it IS in the live registry
    live = c.get("/playbooks").json()
    assert len(live) == 1 and live[0]["hitl_mode"] == "hitl"


def test_reject_does_not_register():
    c = _client(_StubAuthor((_draft_playbook(), "r")))
    pid = c.post("/playbooks/proposed",
                 json={"situation": _situation_json(), "requested_by": "oncall-alice"}).json()["id"]
    resp = c.post(f"/playbooks/proposed/{pid}/reject", json={"decided_by": "oncall-alice"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert c.get("/playbooks").json() == []


def test_approve_forbidden_and_unknown():
    c = _client(_StubAuthor((_draft_playbook(), "r")))
    pid = c.post("/playbooks/proposed",
                 json={"situation": _situation_json(), "requested_by": "oncall-alice"}).json()["id"]
    assert c.post(f"/playbooks/proposed/{pid}/approve", json={"decided_by": "random-bob"}).status_code == 403
    assert c.post("/playbooks/proposed/nope/approve", json={"decided_by": "oncall-alice"}).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/governance/tests/test_proposed_routes.py -v`
Expected: FAIL — the routes don't exist (404s everywhere).

- [ ] **Step 3: Add the factory + state wiring + routes**

In `services/governance/app.py`:

Add imports for `NullRunbookAuthor`/`OpenAICompatibleRunbookAuthor`, `InMemoryProposedPlaybookStore`, `ProposedPlaybook`/`ProposedPlaybookStatus`, and `uuid4`.

Add the factory (near the top, after imports):

```python
def _make_runbook_author(settings):
    if settings.runbook_author_mode == "openai" and settings.llm_runbook_endpoint:
        return OpenAICompatibleRunbookAuthor(
            settings.llm_runbook_endpoint,
            settings.llm_runbook_model,
            api_key=settings.llm_runbook_api_key,
            timeout_seconds=settings.llm_runbook_timeout_seconds,
        )
    return NullRunbookAuthor()
```

In `_init_state`, add:
```python
    app.state.proposed_store = InMemoryProposedPlaybookStore()
    app.state.runbook_author = _make_runbook_author(settings)
```

Add request body models (near the other Pydantic route models like `Decision`/`Graduate`):
```python
class ProposeRequest(BaseModel):
    situation: Situation
    hint: str | None = None
    requested_by: str


class ProposalDecision(BaseModel):
    decided_by: str
```

Add the routes (mirror `decide_approval`/`graduate_playbook`):
```python
@app.post("/playbooks/proposed")
def propose_playbook(body: ProposeRequest) -> ProposedPlaybook:
    if not app.state.rbac.check(body.requested_by, "approve", "playbook:*"):
        raise HTTPException(status_code=403, detail="requester lacks permission")
    drafted = app.state.runbook_author.draft(body.situation, body.hint)
    if drafted is None:
        raise HTTPException(status_code=422, detail="author could not produce a valid runbook")
    playbook, rationale = drafted
    # normalize: force HITL and a server-assigned id (the AI never sets these).
    normalized = playbook.model_copy(
        update={"hitl_mode": HitlMode.HITL, "id": f"ai-{body.situation.signature}-{uuid4().hex[:6]}"}
    )
    proposal = ProposedPlaybook(
        id=f"prop-{uuid4().hex[:8]}",
        playbook=normalized,
        proposed_by="runbook-author",
        rationale=rationale,
        source_situation_id=body.situation.id,
        ts=datetime.now(UTC),
    )
    app.state.proposed_store.add(proposal)
    app.state.audit_sink.write(AuditRecord(
        actor=body.requested_by, action="propose", resource=f"proposal:{proposal.id}",
        decision="allow", ts=datetime.now(UTC), correlation_id=body.situation.id))
    return proposal


@app.get("/playbooks/proposed")
def list_proposed(status: str | None = None) -> list[ProposedPlaybook]:
    st = ProposedPlaybookStatus(status) if status else None
    return app.state.proposed_store.list(status=st)


@app.get("/playbooks/proposed/{proposal_id}")
def get_proposed(proposal_id: str) -> ProposedPlaybook:
    p = app.state.proposed_store.get(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return p


@app.post("/playbooks/proposed/{proposal_id}/approve")
def approve_proposed(proposal_id: str, body: ProposalDecision) -> ProposedPlaybook:
    p = app.state.proposed_store.get(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not app.state.rbac.check(body.decided_by, "approve", f"playbook:{p.playbook.id}"):
        raise HTTPException(status_code=403, detail="decider lacks approve permission")
    updated = app.state.proposed_store.set_status(
        proposal_id, ProposedPlaybookStatus.APPROVED, body.decided_by)
    app.state.playbook_store.register(updated.playbook)  # enters the live registry
    app.state.audit_sink.write(AuditRecord(
        actor=body.decided_by, action="approve-proposal", resource=f"proposal:{proposal_id}",
        decision="allow", ts=datetime.now(UTC), correlation_id=proposal_id))
    return updated


@app.post("/playbooks/proposed/{proposal_id}/reject")
def reject_proposed(proposal_id: str, body: ProposalDecision) -> ProposedPlaybook:
    p = app.state.proposed_store.get(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not app.state.rbac.check(body.decided_by, "reject", f"playbook:{p.playbook.id}"):
        raise HTTPException(status_code=403, detail="decider lacks reject permission")
    updated = app.state.proposed_store.set_status(
        proposal_id, ProposedPlaybookStatus.REJECTED, body.decided_by)
    app.state.audit_sink.write(AuditRecord(
        actor=body.decided_by, action="reject-proposal", resource=f"proposal:{proposal_id}",
        decision="allow", ts=datetime.now(UTC), correlation_id=proposal_id))
    return updated


@app.post("/reset-proposed")
def reset_proposed() -> dict:
    store = getattr(app.state, "proposed_store", None)
    if store is not None:
        store.clear()
    return {"reset": True}
```

Confirm `BaseModel`, `Situation`, `HitlMode`, `AuditRecord`, `datetime`/`UTC`, `HTTPException` are imported at the top of `app.py` (most already are — add `Situation`/`HitlMode`/`ProposedPlaybook`/`ProposedPlaybookStatus`/`uuid4` as needed).

- [ ] **Step 4: Run the route tests**

Run: `uv run pytest services/governance/tests/test_proposed_routes.py -v`
Expected: PASS (all 7).

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. The existing governance tests must be unaffected (the new routes are additive; `_init_state` gains two `app.state` attrs but existing tests set their own `app.state` in `_client()`).

- [ ] **Step 6: Commit**

```bash
git add services/governance/app.py services/governance/tests/test_proposed_routes.py
git commit -m "feat(runbooks): governance propose/approve/reject routes (RBAC-gated, audited)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Frontend + docs

**Files:**
- Modify: `frontend/src/data/types.ts` (add `ProposedPlaybook` + status)
- Modify/create: a proposals view/panel + a "Draft a runbook with AI" affordance on a gap incident + a mock proposal
- Modify: `deploy/k8s/README.md` (or a governance README section) — the propose→approve flow + safety guarantees
- Modify: frontend data-access layer (wherever API calls live) to add the propose/approve/reject calls

**Read first:** how the frontend currently calls governance (search `frontend/src` for existing `/playbooks` or `/approvals` fetches, and how `Incidents.tsx` shows an incident with no `suggested_runbook_id`). Match the existing data-access + view conventions.

- [ ] **Step 1: Add the TS type**

In `frontend/src/data/types.ts`:
```typescript
export type ProposedPlaybookStatus = "proposed" | "approved" | "rejected";

export interface ProposedPlaybook {
  id: string;
  playbook: Playbook;               // reuse the existing Playbook type (add one if absent)
  status: ProposedPlaybookStatus;
  proposed_by: string;
  rationale?: string | null;
  source_situation_id?: string | null;
  decided_by?: string | null;
  ts: number | string;
}
```
If the frontend has no `Playbook` type yet, add a minimal one matching the contract (id/name/match_rule/steps/hitl_mode/reversible), or type `playbook` structurally with the fields the queue renders.

- [ ] **Step 2: Add the "Draft a runbook with AI" affordance**

On an incident whose outcome/diagnosis has no suggested runbook (the gap), render a button that POSTs to `/playbooks/proposed` with `{situation, requested_by}`. On success, surface a toast ("Draft created — review in Proposals") and/or navigate to the proposals view. In mock mode, simulate by adding a mock proposal locally.

- [ ] **Step 3: Add the proposals queue view/panel**

A view (or panel) listing `proposed` items: each shows the drafted playbook's name + steps + rationale + source situation, with **Approve** / **Reject** buttons hitting `/playbooks/proposed/{id}/approve|reject` with `{decided_by}`. Show status transitions. Honest, real data — exactly what the AI drafted, the human as the gate. Seed one mock proposal so the queue renders without an LLM. Match the existing view/styling conventions (Tailwind tokens as elsewhere).

- [ ] **Step 4: Frontend build**

Run: `npm --prefix frontend run build`
Expected: clean (no TS errors).

- [ ] **Step 5: Docs**

Add a section (to `deploy/k8s/README.md` or a governance README) covering: the propose→approve flow (human-initiated draft → proposal → RBAC-gated approve → live registry); the safety guarantees (closed Literal rejects unsafe actions at parse; hitl forced; id server-assigned; only the approve route reaches the registry; approve/reject audited); that the author is `off` by default and opt-in via `runbook_author_mode=openai` + `llm_runbook_endpoint`; and that an approved playbook is thereafter subject to every execution gate (denylist + sandbox + HITL). Reference the design note.

- [ ] **Step 6: Final full gates**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && npm --prefix frontend run build`
Expected: all green + clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/ deploy/k8s/README.md docs/superpowers/specs/2026-09-04-ai-authored-runbooks-design.md docs/superpowers/plans/2026-09-04-ai-authored-runbooks.md
git commit -m "feat(runbooks): proposals queue UI + draft-with-AI affordance; docs, spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 contracts → Task 1; §2 interface → Task 1; §3 adapters → Task 2; §4 store → Task 3; §5 config → Task 1; §6 routes → Task 4; §7 frontend → Task 5; docs (AC8) → Task 5. Acceptance criteria 1–8 all mapped.
- **Safety invariant provable at each step:** Task 1 proves an unsafe inner action fails to construct a `ProposedPlaybook`; Task 2 proves the author returns `None` on an unsafe-action draft and on every failure path (never raises); Task 4 proves the propose route stores-not-registers, forces HITL, server-assigns the id, and the ONLY registry path is the RBAC-gated approve route (403/404 covered).
- **Type consistency:** `ProposedPlaybook` fields (id/playbook/status/proposed_by/rationale/source_situation_id/decided_by/ts) identical across the contract (Task 1), the store (Task 3), the routes (Task 4), and the TS type (Task 5). `RunbookAuthor.draft` returns `tuple[Playbook, str | None] | None` consistently in Task 1 (protocol), Task 2 (impls), Task 4 (route consumes it).
- **Additive/no-regression:** every new field/route is additive; `runbook_author_mode` defaults `off` → `NullRunbookAuthor`; existing governance tests set their own `app.state`, so the two new `_init_state` attrs don't affect them; the base suite stays green (asserted in each task's Step with the full-suite run).
- **No execution-path touch:** PR C never edits `remediate.py`; an approved playbook enters the same `PlaybookStore.register` and inherits every existing gate (denylist + sandbox + HITL) — stated in Global Constraints and the docs task.
- **Reused, not reinvented:** the author mirrors `explanation_provider.py`; the routes mirror `decide_approval`/`graduate_playbook`; the store mirrors `InMemoryApprovalStore`; the route tests mirror `test_governance_api.py`/`test_decide_rbac.py`; RBAC reuses `approve`/`reject`. No new RBAC, no new cross-service coupling (authoring is on-demand within governance).
