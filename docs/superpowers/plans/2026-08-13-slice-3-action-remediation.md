# Slice 3 — Action / HITL-Gated Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `action-service` — consume `situations.diagnosed`, select a playbook, pass it through three hard safety gates (RBAC fail-closed, reversible-only, HITL), execute reversible remediation via a dry-run Remediator, verify health, roll back on failure, emit a `RemediationOutcome` — and add an RBAC check to governance's approval-decide endpoint.

**Architecture:** A FastAPI service following the correlation/rca pattern (daemon consumer thread via lifespan). Three new pluggable interfaces (`GovernanceGate`, `HealthChecker`; `Remediator` already exists) with in-process/dry-run/fake implementations. The orchestration (`execute_remediation`) enforces the three gates in code and maps every branch to a `RemediationOutcome` (reason encoded in `health_after`). Governance's `/approvals/{id}/decide` gains an RBAC check on the decider.

**Tech Stack:** Python 3.11 · FastAPI · Pydantic v2 · in-memory bus · threading (daemon consumer + poll-with-timeout) · pytest

**Spec:** [docs/superpowers/specs/2026-08-13-slice-3-action-remediation-design.md](../specs/2026-08-13-slice-3-action-remediation-design.md)

## Global Constraints

- **Python floor:** 3.11. **Package manager:** `uv` only (`uv add`, `uv run`). Never bare `pip`.
- **Pydantic v2** API only. **Lint gate:** `uv run ruff check .` must pass (0 errors) — apply ruff's UP017/F401 autofix where it fires; that is expected and touches only tokens/imports, never logic.
- **Frozen contracts NOT modified.** Reuse existing: `RemediationOutcome(situation_id, playbook_id, result: RemediationResult, health_after: str, ts: datetime)`; `RemediationResult` = success | failure | rolled_back; `Playbook(id, name, match_rule, steps: list[str], hitl_mode: HitlMode, reversible: bool, rollback_steps: list[str])`; `HitlMode` = auto | hitl | disabled; `ApprovalRequest(id, situation_id, playbook_id, requested_by, status: str = "pending", decided_by: str | None)`; `AuditRecord(actor, action, resource, decision, ts, correlation_id)`; `DiagnosedSituation(situation: Situation, hypotheses, suggested_runbook_id: str | None)`; `Situation(id, ...)`.
- **Bus transport:** all models via `common.envelope` (`publish_model`, `decode_model`, `iter_models`) as `{"data": json}`. Never hand-roll.
- **Topics:** action consumes `situations.diagnosed`, publishes `remediation.outcomes`.
- **Adapters behind interfaces:** action depends on `GovernanceGate`/`Remediator`/`HealthChecker` protocols; tests bind fakes.
- **Three hard gates (the point of the slice):** RBAC fail-closed (no allow → no execute), reversible-only (non-reversible → refused, no execute), HITL (hitl playbook needs explicit "approved"; reject/timeout → no execute). Every "no execute" path must be PROVEN by a test that asserts the RecordingRemediator's `execute` was NOT called.
- **Determinism:** the only wall-clock use is the HITL poll timeout (unavoidable). Tests inject tiny `hitl_poll_timeout_seconds`/`hitl_poll_interval_seconds` and a fake gate so outcomes are deterministic. No wall-clock in gate/outcome logic.
- **Test command:** `uv run pytest` from repo root. **Lint:** `uv run ruff check .`.

---

### Task 1: New interfaces + config

**Files:**
- Modify: `common/interfaces.py` (append `GovernanceGate`, `HealthChecker`; add `ApprovalRequest`, `AuditRecord`... to imports as needed)
- Modify: `common/config.py` (add 2 settings)
- Test: `tests/test_slice3_interfaces.py`

**Interfaces:**
- Consumes: existing `common.contracts` (`ApprovalRequest`, `AuditRecord`, `Situation`).
- Produces:
  - `GovernanceGate` Protocol (runtime_checkable): `check_rbac(actor: str, action: str, resource: str) -> bool`, `request_approval(request: ApprovalRequest) -> ApprovalRequest`, `await_decision(approval_id: str, timeout_seconds: float) -> ApprovalRequest`, `write_audit(record: AuditRecord) -> None`.
  - `HealthChecker` Protocol (runtime_checkable): `check(situation: Situation) -> bool`.
  - `Settings` gains `hitl_poll_timeout_seconds: float = 30.0`, `hitl_poll_interval_seconds: float = 0.5`.

- [ ] **Step 1: Write the failing test**

`tests/test_slice3_interfaces.py`:

```python
from common.config import get_settings
from common.interfaces import GovernanceGate, HealthChecker


def test_governance_gate_runtime_checkable():
    class FakeGate:
        def check_rbac(self, actor, action, resource):
            return True

        def request_approval(self, request):
            return request

        def await_decision(self, approval_id, timeout_seconds):
            return None

        def write_audit(self, record): ...

    assert isinstance(FakeGate(), GovernanceGate)


def test_health_checker_runtime_checkable():
    class FakeHealth:
        def check(self, situation):
            return True

    assert isinstance(FakeHealth(), HealthChecker)


def test_settings_have_hitl_timeouts():
    s = get_settings()
    assert isinstance(s.hitl_poll_timeout_seconds, float)
    assert isinstance(s.hitl_poll_interval_seconds, float)
    assert s.hitl_poll_timeout_seconds > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_slice3_interfaces.py -v`
Expected: FAIL — `ImportError: cannot import name 'GovernanceGate'`.

- [ ] **Step 3: Append the interfaces**

At the top of `common/interfaces.py`, change the contracts import to include `ApprovalRequest`:

```python
from common.contracts import ApprovalRequest, AuditRecord, Playbook, Situation, TelemetryEvent
```

Append to `common/interfaces.py`:

```python
@runtime_checkable
class GovernanceGate(Protocol):
    """The synchronous action→governance seam: RBAC, approvals, audit (ADR-003)."""

    def check_rbac(self, actor: str, action: str, resource: str) -> bool: ...

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest: ...

    def await_decision(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest: ...

    def write_audit(self, record: AuditRecord) -> None: ...


@runtime_checkable
class HealthChecker(Protocol):
    """Post-remediation health signal (ADR-007 verify step)."""

    def check(self, situation: Situation) -> bool: ...
```

- [ ] **Step 4: Add the config settings**

In `common/config.py`, add to the `Settings` class body (after the Slice-2 paths):

```python
    hitl_poll_timeout_seconds: float = 30.0
    hitl_poll_interval_seconds: float = 0.5
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_slice3_interfaces.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add common/interfaces.py common/config.py tests/test_slice3_interfaces.py
git commit -m "feat: add GovernanceGate + HealthChecker interfaces and HITL timeout config"
```

---

### Task 2: Governance RBAC-on-decide

**Files:**
- Modify: `services/governance/app.py` (add RBAC check to `POST /approvals/{id}/decide`)
- Test: `services/governance/tests/test_decide_rbac.py`

**Interfaces:**
- Consumes: `app.state.rbac` (a `RbacPolicy` with `.check`), `app.state.approvals`.
- Produces: `POST /approvals/{id}/decide` now returns **403** if `app.state.rbac.check(decided_by, "approve", f"playbook:{req.playbook_id}")` is False; otherwise 200 + updated `ApprovalRequest` (unchanged behavior).

- [ ] **Step 1: Write the failing test**

`services/governance/tests/test_decide_rbac.py`:

```python
from fastapi.testclient import TestClient

from common.contracts import ApprovalRequest
from services.governance.rbac import RbacPolicy


def _client():
    from services.governance.app import app

    app.state.rbac = RbacPolicy(
        roles={"approver": [{"action": "approve", "resource": "playbook:*"}]},
        actors={"oncall-alice": ["approver"], "random-bob": []},
    )
    app.state.approvals = {
        "a1": ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        ),
    }
    return TestClient(app)


def test_authorized_decider_approves():
    c = _client()
    resp = c.post(
        "/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["decided_by"] == "oncall-alice"


def test_unauthorized_decider_forbidden():
    c = _client()
    resp = c.post("/approvals/a1/decide", json={"decision": "approved", "decided_by": "random-bob"})
    assert resp.status_code == 403
    # the approval must remain pending — no state change on a forbidden decide
    assert (
        c.post(
            "/approvals/a1/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
        ).json()["status"]
        == "approved"
    )


def test_decide_missing_approval_still_404():
    c = _client()
    resp = c.post(
        "/approvals/missing/decide", json={"decision": "approved", "decided_by": "oncall-alice"}
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_decide_rbac.py -v`
Expected: FAIL — `test_unauthorized_decider_forbidden` gets 200 instead of 403 (no RBAC check yet).

- [ ] **Step 3: Add the RBAC check to the decide endpoint**

In `services/governance/app.py`, replace the `decide_approval` function body with:

```python
@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, decision: Decision) -> ApprovalRequest:
    req = app.state.approvals.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    if not app.state.rbac.check(decision.decided_by, "approve", f"playbook:{req.playbook_id}"):
        raise HTTPException(status_code=403, detail="decider lacks approve permission")
    updated = req.model_copy(
        update={"status": decision.decision, "decided_by": decision.decided_by}
    )
    app.state.approvals[approval_id] = updated
    return updated
```

Note: the 404 check comes BEFORE the RBAC check (a missing approval is 404 regardless of decider).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/governance/tests/test_decide_rbac.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the existing governance API tests (no regression)**

Run: `uv run pytest services/governance/tests/ -v`
Expected: after the fix below, PASS.

**Required adjustment to the pre-existing `test_governance_api.py`:** its shared `_client()` helper
(lines 18-21) sets `app.state.rbac` to a policy where the decider `oncall-alice` has NO roles — so
`test_approval_create_and_decide` (which decides with `oncall-alice`) will now get **403** instead of
200 once the RBAC-on-decide check exists. This is correct behavior, so the pre-existing test's fixture
must give its decider the approve permission. In `services/governance/tests/test_governance_api.py`,
change the `_client()` rbac block from:

```python
    app.state.rbac = RbacPolicy(
        roles={"operator": [{"action": "diagnose", "resource": "situation:*"}]},
        actors={"rca-service": ["operator"]},
    )
```

to:

```python
app.state.rbac = RbacPolicy(
    roles={
        "operator": [{"action": "diagnose", "resource": "situation:*"}],
        "approver": [{"action": "approve", "resource": "playbook:*"}],
    },
    actors={"rca-service": ["operator"], "oncall-alice": ["approver"]},
)
```

Leave the rest of `test_governance_api.py` unchanged. (`test_rbac_check` in that file asserts
`rca-service` cannot `approve` — still true, `rca-service` is only an operator — so it stays green.)
This is the one pre-existing test that the new gate breaks; the change gives its decider the
permission the endpoint now requires.

- [ ] **Step 6: Commit**

```bash
git add services/governance/app.py services/governance/tests/test_decide_rbac.py services/governance/tests/test_governance_api.py
git commit -m "feat: enforce RBAC on governance approval decisions (403 if decider lacks approve)"
```

---

### Task 3: Remediator adapters (dry-run + recording)

**Files:**
- Create: `services/action/__init__.py` (if missing — exists from Slice 0, leave as-is if present)
- Create: `services/action/adapters/__init__.py`, `services/action/adapters/remediator.py`
- Test: `services/action/tests/__init__.py`, `services/action/tests/test_remediator.py`

**Interfaces:**
- Consumes: `common.interfaces.Remediator` (existing: `execute(steps: list[str]) -> bool`, `rollback(steps: list[str]) -> bool`).
- Produces:
  - `DryRunRemediator()` — `execute`/`rollback` return True, no side effects. Satisfies `Remediator`.
  - `RecordingRemediator(execute_result: bool = True, rollback_result: bool = True)` — records `executed_steps` and `rolled_back_steps` (lists), returns the injected results. Satisfies `Remediator`.

- [ ] **Step 1: Write the failing test**

`services/action/tests/__init__.py`: (empty file)

`services/action/tests/test_remediator.py`:

```python
from common.interfaces import Remediator
from services.action.adapters.remediator import DryRunRemediator, RecordingRemediator


def test_dryrun_satisfies_protocol_and_succeeds():
    r = DryRunRemediator()
    assert isinstance(r, Remediator)
    assert r.execute(["kubectl rollout restart deploy/web"]) is True
    assert r.rollback(["kubectl rollout undo deploy/web"]) is True


def test_recording_captures_calls():
    r = RecordingRemediator()
    r.execute(["step-a", "step-b"])
    r.rollback(["undo-a"])
    assert r.executed_steps == ["step-a", "step-b"]
    assert r.rolled_back_steps == ["undo-a"]


def test_recording_injects_results():
    r = RecordingRemediator(execute_result=False, rollback_result=True)
    assert r.execute(["x"]) is False
    assert r.rollback(["y"]) is True


def test_recording_satisfies_protocol():
    assert isinstance(RecordingRemediator(), Remediator)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_remediator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.action.adapters'`.

- [ ] **Step 3: Write the remediators**

`services/action/adapters/__init__.py`:

```python
"""Action adapters: remediators, health checkers, governance gate."""
```

`services/action/adapters/remediator.py`:

```python
"""Remediator implementations.

DryRunRemediator is the safe running-service default: it logs the steps and
succeeds without touching real infrastructure. RecordingRemediator is the test
double that captures execute/rollback calls — the safety assertions check
whether execute was (or was NOT) called. Real K8s/Ansible remediators are
deferred (see ADR-007)."""

from __future__ import annotations

import logging

logger = logging.getLogger("intelliops.action.remediator")


class DryRunRemediator:
    def execute(self, steps: list[str]) -> bool:
        for step in steps:
            logger.info("DRY-RUN execute: %s", step)
        return True

    def rollback(self, steps: list[str]) -> bool:
        for step in steps:
            logger.info("DRY-RUN rollback: %s", step)
        return True


class RecordingRemediator:
    def __init__(self, execute_result: bool = True, rollback_result: bool = True) -> None:
        self._execute_result = execute_result
        self._rollback_result = rollback_result
        self.executed_steps: list[str] = []
        self.rolled_back_steps: list[str] = []

    def execute(self, steps: list[str]) -> bool:
        self.executed_steps = list(steps)
        return self._execute_result

    def rollback(self, steps: list[str]) -> bool:
        self.rolled_back_steps = list(steps)
        return self._rollback_result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/action/tests/test_remediator.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/action/adapters/ services/action/tests/
git commit -m "feat: add dry-run and recording remediators"
```

---

### Task 4: HealthChecker adapters

**Files:**
- Create: `services/action/adapters/health.py`
- Test: `services/action/tests/test_health.py`

**Interfaces:**
- Consumes: `common.interfaces.HealthChecker`; `common.contracts.Situation`.
- Produces:
  - `AlwaysHealthyChecker()` — `check(situation)` returns True. Satisfies `HealthChecker`.
  - `FixedHealthChecker(healthy: bool)` — `check` returns the fixed value (the test double for driving the rollback path). Satisfies `HealthChecker`.

- [ ] **Step 1: Write the failing test**

`services/action/tests/test_health.py`:

```python
from datetime import UTC, datetime

from common.contracts import Situation, SituationStatus, TelemetryEvent, TelemetryKind
from common.interfaces import HealthChecker
from services.action.adapters.health import AlwaysHealthyChecker, FixedHealthChecker

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def test_always_healthy():
    c = AlwaysHealthyChecker()
    assert isinstance(c, HealthChecker)
    assert c.check(_situation()) is True


def test_fixed_health_checker():
    assert FixedHealthChecker(healthy=True).check(_situation()) is True
    assert FixedHealthChecker(healthy=False).check(_situation()) is False


def test_fixed_satisfies_protocol():
    assert isinstance(FixedHealthChecker(healthy=True), HealthChecker)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.action.adapters.health'`.

- [ ] **Step 3: Write the health checkers**

`services/action/adapters/health.py`:

```python
"""HealthChecker implementations.

AlwaysHealthyChecker pairs with the dry-run remediator (nothing really changed,
so health is unchanged). FixedHealthChecker is the test double that lets a test
drive the rollback path by returning unhealthy. A real checker (re-query
Prometheus / pod status) is deferred (see ADR-007)."""

from __future__ import annotations

from common.contracts import Situation


class AlwaysHealthyChecker:
    def check(self, situation: Situation) -> bool:
        return True


class FixedHealthChecker:
    def __init__(self, healthy: bool) -> None:
        self._healthy = healthy

    def check(self, situation: Situation) -> bool:
        return self._healthy
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/action/tests/test_health.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/action/adapters/health.py services/action/tests/test_health.py
git commit -m "feat: add health checker adapters"
```

---

### Task 5: InProcessGovernanceGate

**Files:**
- Create: `services/action/adapters/governance_gate.py`
- Test: `services/action/tests/test_governance_gate.py`

**Interfaces:**
- Consumes: `common.interfaces.GovernanceGate`; `common.contracts` (`ApprovalRequest`, `AuditRecord`); `services.governance.rbac.RbacPolicy`; `services.governance.adapters.audit_sink.InMemoryAuditSink`.
- Produces: `InProcessGovernanceGate(rbac, approvals: dict, audit_sink, poll_interval_seconds: float = 0.5)` satisfying `GovernanceGate`:
  - `check_rbac(actor, action, resource)` → `rbac.check(...)`.
  - `request_approval(request)` → stores it in `approvals` (pending), returns it.
  - `await_decision(approval_id, timeout_seconds)` → polls `approvals[approval_id]` every `poll_interval_seconds` until `status != "pending"` or the timeout; returns the (possibly still-pending) request.
  - `write_audit(record)` → `audit_sink.write(record)`.
  - Shares the SAME `approvals` dict and `audit_sink` a governance app would use (passed in).

- [ ] **Step 1: Write the failing test**

`services/action/tests/test_governance_gate.py`:

```python
import threading
from datetime import UTC, datetime

from common.contracts import ApprovalRequest, AuditRecord
from common.interfaces import GovernanceGate
from services.action.adapters.governance_gate import InProcessGovernanceGate
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _gate(approvals=None, poll=0.01):
    rbac = RbacPolicy(
        roles={"operator": [{"action": "execute", "resource": "playbook:*"}]},
        actors={"action-service": ["operator"]},
    )
    return InProcessGovernanceGate(
        rbac,
        approvals if approvals is not None else {},
        InMemoryAuditSink(),
        poll_interval_seconds=poll,
    )


def test_satisfies_protocol():
    assert isinstance(_gate(), GovernanceGate)


def test_check_rbac_delegates():
    g = _gate()
    assert g.check_rbac("action-service", "execute", "playbook:restart-pod") is True
    assert g.check_rbac("action-service", "approve", "playbook:x") is False


def test_request_approval_stores_pending():
    approvals = {}
    g = _gate(approvals)
    req = ApprovalRequest(
        id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
    )
    out = g.request_approval(req)
    assert out.status == "pending"
    assert approvals["a1"].status == "pending"


def test_await_decision_returns_when_approved():
    approvals = {
        "a1": ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        )
    }
    g = _gate(approvals, poll=0.01)

    # a background thread approves after a moment
    def approve():
        approvals["a1"] = approvals["a1"].model_copy(
            update={"status": "approved", "decided_by": "oncall-alice"}
        )

    timer = threading.Timer(0.03, approve)
    timer.start()
    decided = g.await_decision("a1", timeout_seconds=2.0)
    timer.cancel()
    assert decided.status == "approved"


def test_await_decision_times_out_still_pending():
    approvals = {
        "a1": ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        )
    }
    g = _gate(approvals, poll=0.01)
    decided = g.await_decision("a1", timeout_seconds=0.05)
    assert decided.status == "pending"  # caller treats still-pending as timeout (fail closed)


def test_write_audit_persists():
    sink = InMemoryAuditSink()
    rbac = RbacPolicy(roles={}, actors={})
    g = InProcessGovernanceGate(rbac, {}, sink, poll_interval_seconds=0.01)
    g.write_audit(
        AuditRecord(
            actor="action-service",
            action="execute",
            resource="playbook:x",
            decision="allow",
            ts=NOW,
            correlation_id="s1",
        )
    )
    assert len(sink.records()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_governance_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.action.adapters.governance_gate'`.

- [ ] **Step 3: Write the gate**

`services/action/adapters/governance_gate.py`:

```python
"""In-process GovernanceGate: action's synchronous seam to governance.

Constructed with references to the SAME RbacPolicy, approval store, and audit
sink governance uses — so it shares state rather than duplicating it (the
consolidation the Slice-2 review flagged). await_decision polls the shared
approval store with a timeout; a still-pending result on timeout lets the
caller fail closed (ADR-003). An HTTP gate is a deferred alternative."""

from __future__ import annotations

import time

from common.contracts import ApprovalRequest, AuditRecord


class InProcessGovernanceGate:
    def __init__(
        self, rbac, approvals: dict, audit_sink, poll_interval_seconds: float = 0.5
    ) -> None:
        self._rbac = rbac
        self._approvals = approvals
        self._audit_sink = audit_sink
        self._poll = poll_interval_seconds

    def check_rbac(self, actor: str, action: str, resource: str) -> bool:
        return self._rbac.check(actor, action, resource)

    def request_approval(self, request: ApprovalRequest) -> ApprovalRequest:
        self._approvals[request.id] = request
        return request

    def await_decision(self, approval_id: str, timeout_seconds: float) -> ApprovalRequest:
        deadline = time.monotonic() + timeout_seconds
        while True:
            req = self._approvals[approval_id]
            if req.status != "pending":
                return req
            if time.monotonic() >= deadline:
                return req
            time.sleep(self._poll)

    def write_audit(self, record: AuditRecord) -> None:
        self._audit_sink.write(record)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/action/tests/test_governance_gate.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add services/action/adapters/governance_gate.py services/action/tests/test_governance_gate.py
git commit -m "feat: add in-process governance gate with poll-based approval wait"
```

---

### Task 6: select_playbook

**Files:**
- Create: `services/action/select.py`
- Test: `services/action/tests/test_select.py`

**Interfaces:**
- Consumes: `common.contracts` (`DiagnosedSituation`, `Playbook`); `common.interfaces.PlaybookStore`.
- Produces: `select_playbook(diagnosed: DiagnosedSituation, store: PlaybookStore) -> Playbook | None` — returns `store.get(diagnosed.suggested_runbook_id)` when `suggested_runbook_id` is set and known, else None.

- [ ] **Step 1: Write the failing test**

`services/action/tests/test_select.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.select import select_playbook
from services.governance.adapters.playbook_store import InMemoryPlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _diagnosed(runbook_id):
    sit = Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )
    return DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id=runbook_id)


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=["s"],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=[],
        )
    )
    return s


def test_selects_known_playbook():
    pb = select_playbook(_diagnosed("restart-pod"), _store())
    assert pb is not None
    assert pb.id == "restart-pod"


def test_none_when_no_runbook_id():
    assert select_playbook(_diagnosed(None), _store()) is None


def test_none_when_unknown_runbook_id():
    assert select_playbook(_diagnosed("does-not-exist"), _store()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_select.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.action.select'`.

- [ ] **Step 3: Write select_playbook**

`services/action/select.py`:

```python
"""Select the remediation playbook for a diagnosed situation.

Connects "what's wrong" (the RCA-suggested runbook id) to "what to do" (the
registered playbook). Returns None when there is no suggestion or the id is
unknown — the caller emits a skipped outcome (see flow.md 5.4)."""

from __future__ import annotations

from common.contracts import DiagnosedSituation, Playbook
from common.interfaces import PlaybookStore


def select_playbook(diagnosed: DiagnosedSituation, store: PlaybookStore) -> Playbook | None:
    runbook_id = diagnosed.suggested_runbook_id
    if runbook_id is None:
        return None
    return store.get(runbook_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/action/tests/test_select.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/action/select.py services/action/tests/test_select.py
git commit -m "feat: add action playbook selection"
```

---

### Task 7: execute_remediation — the three gates + outcome mapping

**Files:**
- Create: `services/action/remediate.py`
- Test: `services/action/tests/test_remediate.py`

**Interfaces:**
- Consumes: `common.contracts` (`Situation`, `Playbook`, `HitlMode`, `ApprovalRequest`, `AuditRecord`, `RemediationOutcome`, `RemediationResult`); `common.interfaces` (`GovernanceGate`, `Remediator`, `HealthChecker`); `RecordingRemediator`, `FixedHealthChecker` (tests).
- Produces: `execute_remediation(situation: Situation, playbook: Playbook, gate: GovernanceGate, remediator: Remediator, health: HealthChecker, timeout_seconds: float, poll_interval_seconds: float) -> RemediationOutcome`. Enforces the three gates and maps every branch to a `RemediationOutcome` per the spec §5 table. Writes an audit record on every branch (`actor="action-service"`, `correlation_id=situation.id`).

- [ ] **Step 1: Write the failing test**

`services/action/tests/test_remediate.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    HitlMode,
    Playbook,
    RemediationResult,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.adapters.health import FixedHealthChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.remediate import execute_remediation

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _playbook(hitl=HitlMode.AUTO, reversible=True):
    return Playbook(
        id="restart-pod",
        name="Restart",
        match_rule="x",
        steps=["do-thing"],
        hitl_mode=hitl,
        reversible=reversible,
        rollback_steps=["undo-thing"],
    )


class FakeGate:
    """A GovernanceGate whose behavior each test controls."""

    def __init__(self, rbac_allow=True, decision_status="approved"):
        self._rbac_allow = rbac_allow
        self._decision_status = decision_status
        self.audits = []

    def check_rbac(self, actor, action, resource):
        return self._rbac_allow

    def request_approval(self, request):
        return request

    def await_decision(self, approval_id, timeout_seconds):
        return ApprovalRequest(
            id=approval_id,
            situation_id="s1",
            playbook_id="restart-pod",
            requested_by="action-service",
            status=self._decision_status,
            decided_by="oncall-alice",
        )

    def write_audit(self, record):
        self.audits.append(record)


def _run(playbook, gate, remediator, health):
    return execute_remediation(
        _situation(),
        playbook,
        gate,
        remediator,
        health,
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
    )


# --- The three gates BLOCK (each asserts execute was NOT called) ---


def test_disabled_playbook_skips_no_execute():
    r = RecordingRemediator()
    out = _run(_playbook(hitl=HitlMode.DISABLED), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "skipped:disabled"
    assert r.executed_steps == []  # SAFETY: nothing executed


def test_non_reversible_refused_no_execute():
    r = RecordingRemediator()
    out = _run(_playbook(reversible=False), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "refused:not-reversible"
    assert r.executed_steps == []  # SAFETY: nothing executed


def test_rbac_denied_no_execute():
    r = RecordingRemediator()
    out = _run(_playbook(), FakeGate(rbac_allow=False), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "denied:rbac"
    assert r.executed_steps == []  # SAFETY: fail closed


def test_hitl_rejected_no_execute():
    r = RecordingRemediator()
    out = _run(
        _playbook(hitl=HitlMode.HITL),
        FakeGate(decision_status="rejected"),
        r,
        FixedHealthChecker(True),
    )
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "aborted:rejected"
    assert r.executed_steps == []  # SAFETY: no execute on reject


def test_hitl_timeout_no_execute():
    r = RecordingRemediator()
    out = _run(
        _playbook(hitl=HitlMode.HITL),
        FakeGate(decision_status="pending"),
        r,
        FixedHealthChecker(True),
    )
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "aborted:timeout"
    assert r.executed_steps == []  # SAFETY: fail closed on timeout


# --- The happy + rollback paths ---


def test_auto_approved_executes_healthy_success():
    r = RecordingRemediator()
    out = _run(_playbook(hitl=HitlMode.AUTO), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.SUCCESS
    assert out.health_after == "healthy"
    assert r.executed_steps == ["do-thing"]  # executed
    assert r.rolled_back_steps == []  # no rollback


def test_hitl_approved_executes():
    r = RecordingRemediator()
    out = _run(
        _playbook(hitl=HitlMode.HITL),
        FakeGate(decision_status="approved"),
        r,
        FixedHealthChecker(True),
    )
    assert out.result == RemediationResult.SUCCESS
    assert r.executed_steps == ["do-thing"]


def test_unhealthy_triggers_rollback():
    r = RecordingRemediator()
    out = _run(_playbook(), FakeGate(), r, FixedHealthChecker(False))
    assert out.result == RemediationResult.ROLLED_BACK
    assert out.health_after == "unhealthy:rolled-back"
    assert r.executed_steps == ["do-thing"]  # executed
    assert r.rolled_back_steps == ["undo-thing"]  # then rolled back


def test_execute_failure_reported():
    r = RecordingRemediator(execute_result=False)
    out = _run(_playbook(), FakeGate(), r, FixedHealthChecker(True))
    assert out.result == RemediationResult.FAILURE
    assert out.health_after == "execute-failed"


def test_outcome_carries_situation_and_playbook_ids():
    out = _run(_playbook(), FakeGate(), RecordingRemediator(), FixedHealthChecker(True))
    assert out.situation_id == "s1"
    assert out.playbook_id == "restart-pod"


def test_audit_written_on_success():
    g = FakeGate()
    _run(_playbook(), g, RecordingRemediator(), FixedHealthChecker(True))
    assert any(a.action == "execute" and a.correlation_id == "s1" for a in g.audits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_remediate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.action.remediate'`.

- [ ] **Step 3: Write the orchestration**

`services/action/remediate.py`:

```python
"""The remediation orchestration: three hard safety gates + outcome mapping.

Enforces (in order): disabled → skip; not reversible → refuse (ADR-007); RBAC
deny → fail closed (ADR-003); HITL → wait for explicit approval, reject/timeout
fail closed (ADR-008). Only then execute; verify health; roll back if unhealthy.
Every branch produces a RemediationOutcome (reason in health_after) and an audit
record threaded by the situation id (see flow.md 5.4)."""

from __future__ import annotations

from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    AuditRecord,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    Situation,
)

_ACTOR = "action-service"


def _outcome(
    situation: Situation, playbook: Playbook, result: RemediationResult, health_after: str
) -> RemediationOutcome:
    return RemediationOutcome(
        situation_id=situation.id,
        playbook_id=playbook.id,
        result=result,
        health_after=health_after,
        ts=datetime.now(UTC),
    )


def _audit(gate, situation: Situation, playbook: Playbook, decision: str) -> None:
    gate.write_audit(
        AuditRecord(
            actor=_ACTOR,
            action="execute",
            resource=f"playbook:{playbook.id}",
            decision=decision,
            ts=datetime.now(UTC),
            correlation_id=situation.id,
        )
    )


def execute_remediation(
    situation: Situation,
    playbook: Playbook,
    gate,
    remediator,
    health,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> RemediationOutcome:
    # Gate 0: disabled playbooks never run.
    if playbook.hitl_mode == HitlMode.DISABLED:
        _audit(gate, situation, playbook, "skipped")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "skipped:disabled")

    # Gate 1: reversible-only (ADR-007) — a non-reversible playbook is refused.
    if not playbook.reversible:
        _audit(gate, situation, playbook, "refused")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "refused:not-reversible")

    # Gate 2: RBAC, fail closed (ADR-003).
    if not gate.check_rbac(_ACTOR, "execute", f"playbook:{playbook.id}"):
        _audit(gate, situation, playbook, "deny")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "denied:rbac")

    # Gate 3: HITL — wait for an explicit human approval (ADR-008).
    if playbook.hitl_mode == HitlMode.HITL:
        request = gate.request_approval(
            ApprovalRequest(
                id=f"appr-{situation.id}",
                situation_id=situation.id,
                playbook_id=playbook.id,
                requested_by=_ACTOR,
            )
        )
        decided = gate.await_decision(request.id, timeout_seconds)
        if decided.status != "approved":
            reason = "aborted:rejected" if decided.status == "rejected" else "aborted:timeout"
            _audit(gate, situation, playbook, "abort")
            return _outcome(situation, playbook, RemediationResult.FAILURE, reason)

    # Execute.
    if not remediator.execute(playbook.steps):
        _audit(gate, situation, playbook, "execute-failed")
        return _outcome(situation, playbook, RemediationResult.FAILURE, "execute-failed")

    # Verify health; roll back if unhealthy.
    if health.check(situation):
        _audit(gate, situation, playbook, "allow")
        return _outcome(situation, playbook, RemediationResult.SUCCESS, "healthy")

    remediator.rollback(playbook.rollback_steps)
    _audit(gate, situation, playbook, "rolled-back")
    return _outcome(situation, playbook, RemediationResult.ROLLED_BACK, "unhealthy:rolled-back")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/action/tests/test_remediate.py -v`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add services/action/remediate.py services/action/tests/test_remediate.py
git commit -m "feat: add remediation orchestration with three safety gates"
```

---

### Task 8: action consumer + lifespan

**Files:**
- Create: `services/action/consumer.py`
- Modify: `services/action/app.py`
- Test: `services/action/tests/test_consumer.py`

**Interfaces:**
- Consumes: `common.envelope` (`iter_models`, `publish_model`); `common.contracts` (`DiagnosedSituation`, `RemediationOutcome`, `RemediationResult`); `select_playbook`; `execute_remediation`; `GovernanceGate`, `Remediator`, `HealthChecker`, `PlaybookStore`.
- Produces:
  - `run_consumer(bus, store, gate, remediator, health, timeout_seconds, poll_interval_seconds, stop_event) -> None` — consumes `situations.diagnosed`; for each `DiagnosedSituation`, `select_playbook`; if None publish a `RemediationOutcome(failure, "skipped:no-playbook")`; else `execute_remediation` → publish the outcome to `remediation.outcomes`. Breaks on stop_event.
  - `services/action/app.py` starts `run_consumer` in a daemon thread via lifespan; `/health` unchanged.

- [ ] **Step 1: Write the failing test**

`services/action/tests/test_consumer.py`:

```python
import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.consumer import run_consumer
from services.governance.adapters.playbook_store import InMemoryPlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class FakeGate:
    def check_rbac(self, actor, action, resource):
        return True

    def request_approval(self, request):
        return request

    def await_decision(self, approval_id, timeout_seconds):
        return None

    def write_audit(self, record): ...


class ScriptedBus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def _diagnosed(runbook_id):
    sit = Situation(
        id="s1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="p",
                kind=TelemetryKind.METRIC,
                name="cpu",
                value=1.0,
                labels={},
                ts=NOW,
                fingerprint="f",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )
    d = DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id=runbook_id)
    return {"data": d.model_dump_json()}


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart",
            match_rule="x",
            steps=["do"],
            hitl_mode=HitlMode.AUTO,
            reversible=True,
            rollback_steps=["undo"],
        )
    )
    return s


def _run(bus):
    run_consumer(
        bus,
        _store(),
        FakeGate(),
        RecordingRemediator(),
        AlwaysHealthyChecker(),
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )


def test_consumer_emits_success_outcome():
    bus = ScriptedBus([_diagnosed("restart-pod")])
    _run(bus)
    outcomes = [m for (t, m) in bus.published if t == "remediation.outcomes"]
    assert len(outcomes) == 1
    o = decode_model(outcomes[0], RemediationOutcome)
    assert o.result == RemediationResult.SUCCESS
    assert o.situation_id == "s1"


def test_consumer_emits_skipped_when_no_playbook():
    bus = ScriptedBus([_diagnosed("unknown-runbook")])
    _run(bus)
    o = decode_model(
        [m for (t, m) in bus.published if t == "remediation.outcomes"][0], RemediationOutcome
    )
    assert o.result == RemediationResult.FAILURE
    assert o.health_after == "skipped:no-playbook"


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield _diagnosed("restart-pod")

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    bus = InfBus([])
    stop = threading.Event()
    stop.set()
    run_consumer(
        bus,
        _store(),
        FakeGate(),
        RecordingRemediator(),
        AlwaysHealthyChecker(),
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        stop_event=stop,
    )
    assert bus.published == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/action/tests/test_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.action.consumer'`.

- [ ] **Step 3: Write the consumer**

`services/action/consumer.py`:

```python
"""Bus consumer for action-service.

Consumes situations.diagnosed, selects a playbook, runs it through the
remediation gates, and publishes a RemediationOutcome on remediation.outcomes.
When no playbook matches, emits a skipped outcome so Slice-4 feedback still sees
the decision. Runs in a daemon thread started by the FastAPI lifespan."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from common.contracts import DiagnosedSituation, RemediationOutcome, RemediationResult
from common.envelope import iter_models, publish_model
from services.action.remediate import execute_remediation
from services.action.select import select_playbook


def run_consumer(
    bus,
    store,
    gate,
    remediator,
    health,
    timeout_seconds: float,
    poll_interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    for diagnosed in iter_models(bus, "situations.diagnosed", "action", DiagnosedSituation):
        if stop_event.is_set():
            break
        situation = diagnosed.situation
        playbook = select_playbook(diagnosed, store)
        if playbook is None:
            outcome = RemediationOutcome(
                situation_id=situation.id,
                playbook_id=diagnosed.suggested_runbook_id or "",
                result=RemediationResult.FAILURE,
                health_after="skipped:no-playbook",
                ts=datetime.now(UTC),
            )
        else:
            outcome = execute_remediation(
                situation,
                playbook,
                gate,
                remediator,
                health,
                timeout_seconds,
                poll_interval_seconds,
            )
        publish_model(bus, "remediation.outcomes", outcome)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/action/tests/test_consumer.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire the lifespan in `services/action/app.py`**

Replace `services/action/app.py` with:

```python
"""Action service: HITL-gated, reversible remediation."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.governance_gate import InProcessGovernanceGate
from services.action.adapters.remediator import DryRunRemediator
from services.action.consumer import run_consumer
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.governance.rbac import RbacPolicy


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    store = FilePlaybookStore(settings.playbook_store_path)
    gate = InProcessGovernanceGate(
        RbacPolicy.from_file(settings.rbac_policy_path),
        {},
        FileAuditSink(settings.audit_store_path),
        poll_interval_seconds=settings.hitl_poll_interval_seconds,
    )
    thread = threading.Thread(
        target=run_consumer,
        args=(
            app.state.bus,
            store,
            gate,
            DryRunRemediator(),
            AlwaysHealthyChecker(),
            settings.hitl_poll_timeout_seconds,
            settings.hitl_poll_interval_seconds,
            stop_event,
        ),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("action-service")
app.router.lifespan_context = lifespan
```

- [ ] **Step 6: Run the action tests + health**

Run: `uv run pytest services/action/ -v`
Expected: PASS. `/health` for action-service still returns `{"service": "action-service", "status": "ok"}` (covered by `tests/test_services.py`).

- [ ] **Step 7: Commit**

```bash
git add services/action/consumer.py services/action/app.py services/action/tests/test_consumer.py
git commit -m "feat: wire action consumer thread via FastAPI lifespan"
```

---

### Task 9: End-to-end acceptance + docs

**Files:**
- Create: `tests/test_slice3_acceptance.py`
- Modify: `README.md` (roadmap: Slice 3 → done; add a Quickstart note line)

**Interfaces:**
- Consumes: everything above — the InProcessGovernanceGate sharing a real approval store + RbacPolicy + audit sink, RecordingRemediator, FixedHealthChecker, run_consumer.
- Produces: an in-process end-to-end test proving (a) hitl→approve→execute→healthy→success and (b) hitl→approve→execute→unhealthy→rolled_back, with the RecordingRemediator asserting the execute/rollback call sequence and the audit trail present.

- [ ] **Step 1: Write the acceptance test**

`tests/test_slice3_acceptance.py`:

```python
"""Slice-3 acceptance: HITL-gated remediation end-to-end, in-process.

Uses the real InProcessGovernanceGate over a shared approval store + RBAC policy
+ audit sink. A background thread posts the human approval (as a ChatOps/UI
would), then the gate's poll returns approved and remediation proceeds."""

import threading
from datetime import UTC, datetime

from common.contracts import (
    ApprovalRequest,
    DiagnosedSituation,
    HitlMode,
    Playbook,
    RemediationOutcome,
    RemediationResult,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model, publish_model
from services.action.adapters.governance_gate import InProcessGovernanceGate
from services.action.adapters.health import FixedHealthChecker
from services.action.adapters.remediator import RecordingRemediator
from services.action.consumer import run_consumer
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class InMemoryBus:
    def __init__(self):
        self.topics: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.topics.setdefault(topic, []).append(message)

    def consume(self, topic, group):
        yield from list(self.topics.get(topic, []))


def _diagnosed():
    sit = Situation(
        id="sit-web-1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name="cpu_usage",
                value=99.0,
                labels={"service": "web"},
                ts=NOW,
                fingerprint="fp",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig-web",
    )
    return DiagnosedSituation(situation=sit, hypotheses=[], suggested_runbook_id="restart-pod")


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="restart-pod",
            name="Restart Pod",
            match_rule="x",
            steps=["kubectl rollout restart deploy/web"],
            hitl_mode=HitlMode.HITL,
            reversible=True,
            rollback_steps=["kubectl rollout undo deploy/web"],
        )
    )
    return s


def _rbac():
    return RbacPolicy(
        roles={
            "operator": [{"action": "execute", "resource": "playbook:*"}],
            "approver": [{"action": "approve", "resource": "playbook:*"}],
        },
        actors={"action-service": ["operator"], "oncall-alice": ["approver"]},
    )


def _approve_when_pending(approvals, appr_id, audit, done):
    """Simulate a human/ChatOps approving as soon as the request appears."""
    for _ in range(500):
        req = approvals.get(appr_id)
        if req is not None and req.status == "pending":
            approvals[appr_id] = req.model_copy(
                update={"status": "approved", "decided_by": "oncall-alice"}
            )
            done.set()
            return
        threading.Event().wait(0.005)


def test_hitl_approved_healthy_success_end_to_end():
    bus = InMemoryBus()
    approvals: dict = {}
    audit = InMemoryAuditSink()
    remediator = RecordingRemediator()
    gate = InProcessGovernanceGate(_rbac(), approvals, audit, poll_interval_seconds=0.01)
    publish_model(bus, "situations.diagnosed", _diagnosed())

    appr_id = "appr-sit-web-1"
    done = threading.Event()
    approver = threading.Thread(
        target=_approve_when_pending, args=(approvals, appr_id, audit, done), daemon=True
    )
    approver.start()

    run_consumer(
        bus,
        _store(),
        gate,
        remediator,
        FixedHealthChecker(True),
        timeout_seconds=3.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )
    approver.join(timeout=1.0)

    outcomes = bus.topics.get("remediation.outcomes", [])
    assert len(outcomes) == 1
    o = decode_model(outcomes[0], RemediationOutcome)
    assert o.result == RemediationResult.SUCCESS
    assert o.health_after == "healthy"
    assert remediator.executed_steps == ["kubectl rollout restart deploy/web"]
    assert remediator.rolled_back_steps == []  # healthy → no rollback
    assert any(a.action == "execute" and a.correlation_id == "sit-web-1" for a in audit.records())


def test_hitl_approved_unhealthy_rolls_back_end_to_end():
    bus = InMemoryBus()
    approvals: dict = {}
    audit = InMemoryAuditSink()
    remediator = RecordingRemediator()
    gate = InProcessGovernanceGate(_rbac(), approvals, audit, poll_interval_seconds=0.01)
    publish_model(bus, "situations.diagnosed", _diagnosed())

    done = threading.Event()
    approver = threading.Thread(
        target=_approve_when_pending, args=(approvals, "appr-sit-web-1", audit, done), daemon=True
    )
    approver.start()

    run_consumer(
        bus,
        _store(),
        gate,
        remediator,
        FixedHealthChecker(False),
        timeout_seconds=3.0,
        poll_interval_seconds=0.01,
        stop_event=threading.Event(),
    )
    approver.join(timeout=1.0)

    o = decode_model(bus.topics["remediation.outcomes"][0], RemediationOutcome)
    assert o.result == RemediationResult.ROLLED_BACK
    assert remediator.executed_steps == ["kubectl rollout restart deploy/web"]
    assert remediator.rolled_back_steps == ["kubectl rollout undo deploy/web"]  # rolled back
```

- [ ] **Step 2: Run the acceptance test**

Run: `uv run pytest tests/test_slice3_acceptance.py -v`
Expected: PASS (2 passed).

- [ ] **Step 3: Run the full suite + lint**

Run: `uv run pytest` then `uv run ruff check .`
Expected: all pass; ruff clean (apply UP017/F401 autofix if it fires — tokens/imports only, never logic).

- [ ] **Step 4: Update the README roadmap**

In `README.md`, change the Slice 3 roadmap row status from `⏳ planned` to `✅ done` (ONLY the
Slice 3 row; Slice 4 stays `⏳ planned`). Under Quickstart, add this line after the Slice-2 line:

```
> Slice 3 adds action-service (8004): HITL-gated, reversible remediation of `situations.diagnosed` → `remediation.outcomes`, with RBAC-enforced approvals.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_slice3_acceptance.py README.md
git commit -m "test: add slice-3 end-to-end acceptance; mark slice done"
```

---

## Self-Review

**1. Spec coverage** (against the Slice-3 spec §2-4, §5, §9):
- Interfaces `GovernanceGate` + `HealthChecker` (§2.1) → Task 1 ✓
- Config timeouts (§2.2) → Task 1 ✓
- Governance RBAC-on-decide (§4.1) → Task 2 ✓
- Remediator dry-run + recording (§3.2) → Task 3 ✓
- HealthChecker (§3.3) → Task 4 ✓
- InProcessGovernanceGate (§3.4) → Task 5 ✓
- select_playbook (§3.1) → Task 6 ✓
- execute_remediation + three gates + outcome mapping (§3.5, §5) → Task 7 ✓
- action consumer + lifespan (§3.6) → Task 8 ✓
- End-to-end acceptance success + rollback (§7) → Task 9 ✓
- *Deferred by design (not gaps):* real K8s/Ansible, real health probing, HTTP gate, ChatOps, auto-graduation, Slice-4 feedback consumption — all per §11.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code and test step is complete literal content. Task 2 Step 5 spells out the exact adjustment to the one pre-existing test that the new RBAC check breaks (not a placeholder — a concrete instruction with the exact rbac policy to add).

**3. Type consistency:**
- `GovernanceGate` (check_rbac/request_approval/await_decision/write_audit) — Task 1, implemented Task 5, consumed Tasks 7/8/9. Signatures identical. ✓
- `HealthChecker.check(situation)` — Task 1, impl Task 4, consumed 7/8/9. ✓
- `Remediator` (execute/rollback) existing; `RecordingRemediator(execute_result, rollback_result)` with `executed_steps`/`rolled_back_steps` — Task 3, consumed 7/8/9. ✓
- `InProcessGovernanceGate(rbac, approvals, audit_sink, poll_interval_seconds)` — Task 5, used in 8/9. ✓
- `select_playbook(diagnosed, store)` — Task 6, used 8. ✓
- `execute_remediation(situation, playbook, gate, remediator, health, timeout_seconds, poll_interval_seconds)` — Task 7, used 8. ✓
- `run_consumer(bus, store, gate, remediator, health, timeout_seconds, poll_interval_seconds, stop_event)` — Task 8, used 9. ✓
- Outcome `health_after` strings consistent between the orchestration (Task 7) and the spec §5 table and the tests (Tasks 7/8/9): skipped:disabled, refused:not-reversible, denied:rbac, aborted:rejected, aborted:timeout, execute-failed, healthy, unhealthy:rolled-back, skipped:no-playbook. ✓
- Topics `situations.diagnosed` (consumed) / `remediation.outcomes` (published) consistent Tasks 8/9. ✓

One thing verified: Task 7's `execute_remediation` takes `timeout_seconds`/`poll_interval_seconds` as explicit params (not reading settings directly), so it's pure and testable; Task 8's `run_consumer` threads them through from settings; Task 9 passes tiny values. Consistent. And the FakeGate in Task 7's tests returns a decision immediately (no real polling), so those tests don't depend on wall-clock; only Task 5's gate test and the Task 9 acceptance exercise the real poll loop (with tiny intervals + a background approver).
