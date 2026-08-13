# Slice 2 — RCA + Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `governance-service` (audit log, playbook registry, RBAC checks, approval endpoints) and `rca-service` (enrich `situations.detected` → rank root-cause hypotheses → emit `DiagnosedSituation` on `situations.diagnosed`), with an in-process end-to-end acceptance.

**Architecture:** Two FastAPI services following the Slice-1 pattern. Governance persists via pluggable sinks/stores (in-memory for tests, file-backed for the running service; Postgres deferred) and exposes a REST control plane. RCA runs a daemon consumer thread (FastAPI lifespan) that enriches each Situation via a `ContextProvider`, ranks hypotheses with deterministic rules, marks the situation `diagnosed`, publishes a `DiagnosedSituation`, and writes an audit record.

**Tech Stack:** Python 3.11 · FastAPI · Pydantic v2 · Redis Streams / in-memory bus · PyYAML (already present) · fnmatch (stdlib) · pytest

**Spec:** [docs/superpowers/specs/2026-08-13-slice-2-rca-governance-design.md](../specs/2026-08-13-slice-2-rca-governance-design.md)

## Global Constraints

- **Python floor:** 3.11. **Package manager:** `uv` only (`uv add`, `uv run`). Never bare `pip`.
- **Pydantic v2** API only. **Lint gate:** `uv run ruff check .` must pass (0 errors) — apply ruff's UP017 autofix (`timezone.utc`→`datetime.UTC`) where it fires; that is expected and touches only tzinfo tokens.
- **Frozen contracts:** `Situation`, `RootCauseHypothesis`, `Playbook`, `AuditRecord`, `ApprovalRequest` in `common/contracts.py` are NOT modified. New contracts are ADDED.
- **`RootCauseHypothesis`** (existing): `situation_id, description, confidence: float, evidence: list[str], suggested_runbook_id: str | None`.
- **`Playbook`** (existing): `id, name, match_rule, steps: list[str], hitl_mode: HitlMode, reversible: bool, rollback_steps: list[str]`.
- **`AuditRecord`** (existing): `actor, action, resource, decision, ts: datetime, correlation_id`.
- **`ApprovalRequest`** (existing): `id, situation_id, playbook_id, requested_by, status: str, decided_by: str | None`.
- **Bus transport:** all models travel via `common.envelope` (`publish_model`, `decode_model`, `iter_models`) as `{"data": json}`. Never hand-roll serialization.
- **Topics:** rca consumes `situations.detected`, publishes `situations.diagnosed`.
- **PyYAML is already installed** (6.0.3, transitive). `import yaml` works; do NOT run `uv add pyyaml`.
- **Adapters behind interfaces:** services depend on `AuditSink`/`PlaybookStore`/`ContextProvider` protocols, and tests bind in-memory fakes.
- **Determinism:** ranking rules are deterministic functions of (situation, context); no wall-clock reads in ranking.
- **Test command:** `uv run pytest` from repo root. **Lint:** `uv run ruff check .`.

---

### Task 1: New contracts + interfaces + config

**Files:**
- Modify: `common/contracts.py` (append `EnrichmentContext`, `DiagnosedSituation`)
- Modify: `common/interfaces.py` (append `PlaybookStore`, `ContextProvider`)
- Modify: `common/config.py` (add 4 settings)
- Test: `tests/test_slice2_contracts.py`

**Interfaces:**
- Consumes: existing `Situation`, `RootCauseHypothesis` from `common.contracts`.
- Produces:
  - `EnrichmentContext(recent_deploys: list[dict], topology: dict, config_changes: list[dict])` — all default to empty.
  - `DiagnosedSituation(situation: Situation, hypotheses: list[RootCauseHypothesis], suggested_runbook_id: str | None)`.
  - `PlaybookStore` Protocol: `register(playbook) -> None`, `get(playbook_id: str) -> Playbook | None`, `list() -> list[Playbook]`.
  - `ContextProvider` Protocol: `recent_deploys() -> list[dict]`, `topology_for(labels: dict[str, str]) -> dict`, `config_changes() -> list[dict]`.
  - `Settings` gains `audit_store_path`, `playbook_store_path`, `rbac_policy_path`, `rca_context_path` (all str, with defaults).

- [ ] **Step 1: Write the failing test**

`tests/test_slice2_contracts.py`:

```python
from datetime import UTC, datetime

from common.config import get_settings
from common.contracts import (
    DiagnosedSituation,
    EnrichmentContext,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.interfaces import ContextProvider, PlaybookStore

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name="cpu", value=99.0,
            labels={"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


def test_enrichment_context_defaults_empty():
    ctx = EnrichmentContext()
    assert ctx.recent_deploys == []
    assert ctx.topology == {}
    assert ctx.config_changes == []


def test_diagnosed_situation_roundtrips():
    d = DiagnosedSituation(
        situation=_situation(),
        hypotheses=[RootCauseHypothesis(
            situation_id="sit-1", description="recent deploy", confidence=0.8,
            evidence=["deploy web@v2"], suggested_runbook_id="rollback-deploy",
        )],
        suggested_runbook_id="rollback-deploy",
    )
    restored = DiagnosedSituation.model_validate(d.model_dump())
    assert restored == d
    assert restored.hypotheses[0].confidence == 0.8


def test_protocols_are_runtime_checkable():
    class FakeStore:
        def register(self, playbook): ...
        def get(self, playbook_id): return None
        def list(self): return []

    class FakeProvider:
        def recent_deploys(self): return []
        def topology_for(self, labels): return {}
        def config_changes(self): return []

    assert isinstance(FakeStore(), PlaybookStore)
    assert isinstance(FakeProvider(), ContextProvider)


def test_settings_have_slice2_paths():
    s = get_settings()
    assert s.audit_store_path.endswith(".jsonl")
    assert isinstance(s.playbook_store_path, str)
    assert s.rbac_policy_path.endswith(".yaml")
    assert isinstance(s.rca_context_path, str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_slice2_contracts.py -v`
Expected: FAIL — `ImportError: cannot import name 'DiagnosedSituation'`.

- [ ] **Step 3: Append the contracts**

Append to `common/contracts.py`:

```python
class EnrichmentContext(BaseModel):
    """Change/deploy/topology context gathered for a Situation during RCA."""

    recent_deploys: list[dict] = Field(default_factory=list)
    topology: dict = Field(default_factory=dict)
    config_changes: list[dict] = Field(default_factory=list)


class DiagnosedSituation(BaseModel):
    """The currency of situations.diagnosed: a diagnosed Situation plus ranked
    root-cause hypotheses and the top suggested runbook. Additive — does not
    mutate the frozen Situation contract."""

    situation: Situation
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    suggested_runbook_id: str | None = None
```

- [ ] **Step 4: Append the interfaces**

Append to `common/interfaces.py` (note: `Playbook` must be added to the existing
`from common.contracts import ...` line):

```python
@runtime_checkable
class PlaybookStore(Protocol):
    """The CoE playbook registry (in-memory / file / Postgres)."""

    def register(self, playbook: Playbook) -> None: ...

    def get(self, playbook_id: str) -> Playbook | None: ...

    def list(self) -> list[Playbook]: ...


@runtime_checkable
class ContextProvider(Protocol):
    """A source of RCA enrichment context (file / Prometheus / CMDB / git)."""

    def recent_deploys(self) -> list[dict]: ...

    def topology_for(self, labels: dict[str, str]) -> dict: ...

    def config_changes(self) -> list[dict]: ...
```

At the top of `common/interfaces.py`, change the contracts import to include `Playbook`:

```python
from common.contracts import AuditRecord, Playbook, Situation, TelemetryEvent
```

- [ ] **Step 5: Add the config settings**

In `common/config.py`, add these fields to the `Settings` class body (after `redis_url`):

```python
    audit_store_path: str = "data/audit.jsonl"
    playbook_store_path: str = "data/playbooks"
    rbac_policy_path: str = "policies/rbac_policy.yaml"
    rca_context_path: str = "data/rca_context"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_slice2_contracts.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add common/contracts.py common/interfaces.py common/config.py tests/test_slice2_contracts.py
git commit -m "feat: add Slice-2 contracts, interfaces, and config"
```

---

### Task 2: Governance RBAC policy

**Files:**
- Create: `services/governance/__init__.py` (if missing — check first; it exists from Slice 0, leave as-is if present)
- Create: `services/governance/rbac.py`
- Create: `policies/rbac_policy.yaml`
- Test: `services/governance/tests/__init__.py`, `services/governance/tests/test_rbac.py`

**Interfaces:**
- Consumes: PyYAML (`import yaml`), `fnmatch.fnmatch` (stdlib).
- Produces: `RbacPolicy` with `RbacPolicy.from_file(path: str) -> RbacPolicy`, `RbacPolicy(roles: dict, actors: dict)`, and `check(actor: str, action: str, resource: str) -> bool`. Default deny. Resource matching via `fnmatch` glob.

- [ ] **Step 1: Write the failing test**

`services/governance/tests/__init__.py`: (empty file)

`services/governance/tests/test_rbac.py`:

```python
from services.governance.rbac import RbacPolicy

POLICY = {
    "roles": {
        "operator": [
            {"action": "enrich", "resource": "situation:*"},
            {"action": "diagnose", "resource": "situation:*"},
        ],
        "approver": [{"action": "approve", "resource": "playbook:*"}],
    },
    "actors": {
        "rca-service": ["operator"],
        "oncall-alice": ["operator", "approver"],
    },
}


def test_allows_action_within_role():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    assert p.check("rca-service", "diagnose", "situation:sit-1") is True


def test_denies_action_outside_role():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    # rca-service is only an operator, not an approver
    assert p.check("rca-service", "approve", "playbook:restart-pod") is False


def test_multi_role_actor_gets_union():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    assert p.check("oncall-alice", "approve", "playbook:restart-pod") is True
    assert p.check("oncall-alice", "diagnose", "situation:x") is True


def test_resource_glob_scoping():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    # approver may approve playbook:* but not situation:*
    assert p.check("oncall-alice", "approve", "situation:x") is False


def test_unknown_actor_denied():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    assert p.check("mystery", "diagnose", "situation:x") is False


def test_from_file_loads_yaml(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text(
        "roles:\n"
        "  operator:\n"
        "    - {action: diagnose, resource: 'situation:*'}\n"
        "actors:\n"
        "  rca-service: [operator]\n"
    )
    p = RbacPolicy.from_file(str(f))
    assert p.check("rca-service", "diagnose", "situation:abc") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_rbac.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.governance.rbac'`.

- [ ] **Step 3: Write the RBAC policy code**

`services/governance/rbac.py`:

```python
"""Static role→permission RBAC policy.

Actors map to roles; roles map to allowed (action, resource-glob) rules. A
check passes when any rule of any of the actor's roles matches the action
exactly and the resource by fnmatch glob. Default deny (see ADR-003).
"""

from __future__ import annotations

from fnmatch import fnmatch

import yaml


class RbacPolicy:
    def __init__(self, roles: dict, actors: dict) -> None:
        self._roles = roles
        self._actors = actors

    @classmethod
    def from_file(cls, path: str) -> RbacPolicy:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls(roles=data.get("roles", {}), actors=data.get("actors", {}))

    def check(self, actor: str, action: str, resource: str) -> bool:
        for role in self._actors.get(actor, []):
            for rule in self._roles.get(role, []):
                if rule.get("action") == action and fnmatch(resource, rule.get("resource", "")):
                    return True
        return False
```

- [ ] **Step 4: Write the seed policy file**

`policies/rbac_policy.yaml`:

```yaml
roles:
  operator:
    - {action: enrich, resource: "situation:*"}
    - {action: diagnose, resource: "situation:*"}
    - {action: read, resource: "*"}
  approver:
    - {action: approve, resource: "playbook:*"}
    - {action: reject, resource: "playbook:*"}
actors:
  rca-service: [operator]
  action-service: [operator]
  oncall-alice: [operator, approver]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest services/governance/tests/test_rbac.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add services/governance/rbac.py policies/rbac_policy.yaml services/governance/tests/
git commit -m "feat: add governance RBAC policy"
```

---

### Task 3: Governance audit sink

**Files:**
- Create: `services/governance/adapters/__init__.py`, `services/governance/adapters/audit_sink.py`
- Test: `services/governance/tests/test_audit_sink.py`

**Interfaces:**
- Consumes: `common.contracts.AuditRecord`, `common.interfaces.AuditSink`.
- Produces:
  - `InMemoryAuditSink()` with `write(record)` and `records() -> list[AuditRecord]`; satisfies `AuditSink`.
  - `FileAuditSink(path: str)` with `write(record)` (append JSONL) and `records()` (read back); satisfies `AuditSink`. Creates the parent dir if missing.

- [ ] **Step 1: Write the failing test**

`services/governance/tests/test_audit_sink.py`:

```python
from datetime import UTC, datetime

from common.contracts import AuditRecord
from common.interfaces import AuditSink
from services.governance.adapters.audit_sink import FileAuditSink, InMemoryAuditSink

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _record(cid="sit-1"):
    return AuditRecord(
        actor="rca-service", action="diagnose", resource="situation:sit-1",
        decision="allow", ts=NOW, correlation_id=cid,
    )


def test_inmemory_sink_satisfies_protocol():
    assert isinstance(InMemoryAuditSink(), AuditSink)


def test_inmemory_write_and_read():
    sink = InMemoryAuditSink()
    sink.write(_record())
    sink.write(_record("sit-2"))
    assert len(sink.records()) == 2
    assert sink.records()[0].correlation_id == "sit-1"


def test_file_sink_roundtrips(tmp_path):
    path = tmp_path / "sub" / "audit.jsonl"  # parent dir does not exist yet
    sink = FileAuditSink(str(path))
    sink.write(_record())
    sink.write(_record("sit-2"))
    # a fresh sink reads the same file back
    reread = FileAuditSink(str(path)).records()
    assert [r.correlation_id for r in reread] == ["sit-1", "sit-2"]
    assert all(isinstance(r, AuditRecord) for r in reread)


def test_file_sink_satisfies_protocol(tmp_path):
    assert isinstance(FileAuditSink(str(tmp_path / "a.jsonl")), AuditSink)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_audit_sink.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.governance.adapters'`.

- [ ] **Step 3: Write the audit sinks**

`services/governance/adapters/__init__.py`:

```python
"""Governance adapters: audit sinks and playbook stores."""
```

`services/governance/adapters/audit_sink.py`:

```python
"""AuditSink implementations: in-memory (tests) and append-only JSONL file.

The audit log is the immutable compliance backbone (NIST AI RMF). Postgres is a
deferred adapter; the file sink is the running-service default this slice.
"""

from __future__ import annotations

import os

from common.contracts import AuditRecord


class InMemoryAuditSink:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self._records.append(record)

    def records(self) -> list[AuditRecord]:
        return list(self._records)


class FileAuditSink:
    def __init__(self, path: str) -> None:
        self._path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def records(self) -> list[AuditRecord]:
        if not os.path.exists(self._path):
            return []
        out: list[AuditRecord] = []
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(AuditRecord.model_validate_json(line))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/governance/tests/test_audit_sink.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/governance/adapters/ services/governance/tests/test_audit_sink.py
git commit -m "feat: add governance audit sinks (in-memory + file)"
```

---

### Task 4: Governance playbook store + seed playbooks

**Files:**
- Create: `services/governance/adapters/playbook_store.py`
- Create: `playbooks/rollback-deploy.yaml`, `playbooks/scale-service.yaml`, `playbooks/restart-pod.yaml`
- Test: `services/governance/tests/test_playbook_store.py`

**Interfaces:**
- Consumes: `common.contracts.Playbook`, `common.contracts.HitlMode`, `common.interfaces.PlaybookStore`.
- Produces:
  - `InMemoryPlaybookStore()` — `register`/`get`/`list`; satisfies `PlaybookStore`.
  - `FilePlaybookStore(path: str)` — loads all `*.yaml` under `path` on construction, `register` writes a YAML file, `get`/`list` read from the in-memory index; satisfies `PlaybookStore`.
  - `load_seed_playbooks(path: str) -> list[Playbook]` — reads the committed `playbooks/` dir.

- [ ] **Step 1: Write the failing test**

`services/governance/tests/test_playbook_store.py`:

```python
from common.contracts import HitlMode, Playbook
from common.interfaces import PlaybookStore
from services.governance.adapters.playbook_store import (
    FilePlaybookStore,
    InMemoryPlaybookStore,
)


def _playbook(pid="restart-pod"):
    return Playbook(
        id=pid, name="Restart Pod", match_rule="signature == 'x'",
        steps=["kubectl rollout restart deploy/web"], hitl_mode=HitlMode.HITL,
        reversible=True, rollback_steps=["kubectl rollout undo deploy/web"],
    )


def test_inmemory_store_satisfies_protocol():
    assert isinstance(InMemoryPlaybookStore(), PlaybookStore)


def test_inmemory_register_get_list():
    store = InMemoryPlaybookStore()
    store.register(_playbook())
    assert store.get("restart-pod").name == "Restart Pod"
    assert store.get("missing") is None
    assert [p.id for p in store.list()] == ["restart-pod"]


def test_file_store_persists_and_reloads(tmp_path):
    store = FilePlaybookStore(str(tmp_path))
    store.register(_playbook("scale-service"))
    # a fresh store over the same dir sees the registered playbook
    reloaded = FilePlaybookStore(str(tmp_path))
    assert reloaded.get("scale-service") is not None
    assert reloaded.get("scale-service").hitl_mode == HitlMode.HITL


def test_seed_playbooks_load():
    from services.governance.adapters.playbook_store import load_seed_playbooks

    seeds = load_seed_playbooks("playbooks")
    ids = {p.id for p in seeds}
    assert {"rollback-deploy", "scale-service", "restart-pod"} <= ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_playbook_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'FilePlaybookStore'`.

- [ ] **Step 3: Write the seed playbook files**

`playbooks/rollback-deploy.yaml`:

```yaml
id: rollback-deploy
name: Rollback Deployment
match_rule: "hypothesis == 'recent-deploy'"
steps:
  - "kubectl rollout undo deploy/{service}"
hitl_mode: hitl
reversible: true
rollback_steps:
  - "kubectl rollout undo deploy/{service} --to-revision={prev}"
```

`playbooks/scale-service.yaml`:

```yaml
id: scale-service
name: Scale Service Horizontally
match_rule: "hypothesis == 'resource-exhaustion'"
steps:
  - "kubectl scale deploy/{service} --replicas={target}"
hitl_mode: hitl
reversible: true
rollback_steps:
  - "kubectl scale deploy/{service} --replicas={original}"
```

`playbooks/restart-pod.yaml`:

```yaml
id: restart-pod
name: Restart Pod
match_rule: "hypothesis == 'error-spike'"
steps:
  - "kubectl rollout restart deploy/{service}"
hitl_mode: hitl
reversible: true
rollback_steps: []
```

- [ ] **Step 4: Write the playbook store**

`services/governance/adapters/playbook_store.py`:

```python
"""PlaybookStore implementations: in-memory (tests) and YAML-file-backed.

The registry is the CoE's shared playbook catalog — standardized, not
reinvented per team. Postgres is a deferred adapter.
"""

from __future__ import annotations

import glob
import os

import yaml

from common.contracts import Playbook


def load_seed_playbooks(path: str) -> list[Playbook]:
    out: list[Playbook] = []
    for f in sorted(glob.glob(os.path.join(path, "*.yaml"))):
        with open(f, encoding="utf-8") as fh:
            out.append(Playbook.model_validate(yaml.safe_load(fh)))
    return out


class InMemoryPlaybookStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Playbook] = {}

    def register(self, playbook: Playbook) -> None:
        self._by_id[playbook.id] = playbook

    def get(self, playbook_id: str) -> Playbook | None:
        return self._by_id.get(playbook_id)

    def list(self) -> list[Playbook]:
        return list(self._by_id.values())


class FilePlaybookStore:
    def __init__(self, path: str) -> None:
        self._path = path
        os.makedirs(path, exist_ok=True)
        self._by_id: dict[str, Playbook] = {
            p.id: p for p in load_seed_playbooks(path)
        }

    def register(self, playbook: Playbook) -> None:
        self._by_id[playbook.id] = playbook
        with open(os.path.join(self._path, f"{playbook.id}.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(playbook.model_dump(mode="json"), fh)

    def get(self, playbook_id: str) -> Playbook | None:
        return self._by_id.get(playbook_id)

    def list(self) -> list[Playbook]:
        return list(self._by_id.values())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest services/governance/tests/test_playbook_store.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add services/governance/adapters/playbook_store.py playbooks/ services/governance/tests/test_playbook_store.py
git commit -m "feat: add governance playbook store and seed playbooks"
```

---

### Task 5: Governance REST API (audit, playbooks, rbac, approvals)

**Files:**
- Modify: `services/governance/app.py`
- Test: `services/governance/tests/test_governance_api.py`

**Interfaces:**
- Consumes: `services.base.create_app`; `RbacPolicy`; `InMemoryAuditSink`; `InMemoryPlaybookStore`; `common.contracts` (`AuditRecord`, `Playbook`, `ApprovalRequest`); `common.config.get_settings`.
- Produces: governance-service with `POST /audit`, `GET /audit`, `POST /playbooks`, `GET /playbooks`, `GET /playbooks/{id}`, `POST /rbac/check`, `POST /approvals`, `POST /approvals/{id}/decide`. Stores live on `app.state` (`audit_sink`, `playbook_store`, `rbac`, `approvals`) so tests can swap them.

- [ ] **Step 1: Write the failing test**

`services/governance/tests/test_governance_api.py`:

```python
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from common.contracts import HitlMode, Playbook
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.governance.rbac import RbacPolicy

NOW = datetime(2026, 8, 13, tzinfo=UTC).isoformat()


def _client():
    from services.governance.app import app

    app.state.audit_sink = InMemoryAuditSink()
    app.state.playbook_store = InMemoryPlaybookStore()
    app.state.rbac = RbacPolicy(
        roles={"operator": [{"action": "diagnose", "resource": "situation:*"}]},
        actors={"rca-service": ["operator"]},
    )
    app.state.approvals = {}
    return TestClient(app)


def test_health_still_works():
    c = _client()
    assert c.get("/health").json() == {"service": "governance-service", "status": "ok"}


def test_audit_write_and_query():
    c = _client()
    rec = {"actor": "rca-service", "action": "diagnose", "resource": "situation:sit-1",
           "decision": "allow", "ts": NOW, "correlation_id": "sit-1"}
    assert c.post("/audit", json=rec).status_code == 200
    got = c.get("/audit", params={"correlation_id": "sit-1"}).json()
    assert len(got) == 1
    assert got[0]["actor"] == "rca-service"
    # a different correlation_id returns nothing
    assert c.get("/audit", params={"correlation_id": "other"}).json() == []


def test_playbook_register_and_list():
    c = _client()
    pb = Playbook(id="restart-pod", name="Restart Pod", match_rule="x",
                  steps=["s"], hitl_mode=HitlMode.HITL).model_dump(mode="json")
    assert c.post("/playbooks", json=pb).status_code == 200
    assert c.get("/playbooks/restart-pod").json()["name"] == "Restart Pod"
    assert [p["id"] for p in c.get("/playbooks").json()] == ["restart-pod"]
    assert c.get("/playbooks/missing").status_code == 404


def test_rbac_check():
    c = _client()
    allow = c.post("/rbac/check", json={"actor": "rca-service", "action": "diagnose",
                                        "resource": "situation:sit-1"}).json()
    assert allow == {"allowed": True}
    deny = c.post("/rbac/check", json={"actor": "rca-service", "action": "approve",
                                       "resource": "playbook:x"}).json()
    assert deny == {"allowed": False}


def test_approval_create_and_decide():
    c = _client()
    created = c.post("/approvals", json={"id": "a1", "situation_id": "sit-1",
                                         "playbook_id": "restart-pod",
                                         "requested_by": "action-service"}).json()
    assert created["status"] == "pending"
    decided = c.post("/approvals/a1/decide",
                     json={"decision": "approved", "decided_by": "oncall-alice"}).json()
    assert decided["status"] == "approved"
    assert decided["decided_by"] == "oncall-alice"
    assert c.post("/approvals/missing/decide",
                  json={"decision": "approved", "decided_by": "x"}).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/governance/tests/test_governance_api.py -v`
Expected: FAIL — 404s (endpoints not defined).

- [ ] **Step 3: Write the governance app**

Replace `services/governance/app.py` with:

```python
"""Governance service: RBAC gate, audit log, playbook registry, approvals."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.config import get_settings
from common.contracts import ApprovalRequest, AuditRecord, Playbook
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.governance.rbac import RbacPolicy

app = create_app("governance-service")


def _init_state() -> None:
    settings = get_settings()
    app.state.audit_sink = FileAuditSink(settings.audit_store_path)
    app.state.playbook_store = FilePlaybookStore(settings.playbook_store_path)
    app.state.rbac = RbacPolicy.from_file(settings.rbac_policy_path)
    app.state.approvals = {}


_init_state()


class RbacCheck(BaseModel):
    actor: str
    action: str
    resource: str


class Decision(BaseModel):
    decision: str
    decided_by: str


@app.post("/audit")
def write_audit(record: AuditRecord) -> dict[str, str]:
    app.state.audit_sink.write(record)
    return {"status": "ok"}


@app.get("/audit")
def query_audit(correlation_id: str | None = None) -> list[AuditRecord]:
    records = app.state.audit_sink.records()
    if correlation_id is not None:
        records = [r for r in records if r.correlation_id == correlation_id]
    return records


@app.post("/playbooks")
def register_playbook(playbook: Playbook) -> dict[str, str]:
    app.state.playbook_store.register(playbook)
    return {"status": "ok"}


@app.get("/playbooks")
def list_playbooks() -> list[Playbook]:
    return app.state.playbook_store.list()


@app.get("/playbooks/{playbook_id}")
def get_playbook(playbook_id: str) -> Playbook:
    pb = app.state.playbook_store.get(playbook_id)
    if pb is None:
        raise HTTPException(status_code=404, detail="playbook not found")
    return pb


@app.post("/rbac/check")
def rbac_check(body: RbacCheck) -> dict[str, bool]:
    return {"allowed": app.state.rbac.check(body.actor, body.action, body.resource)}


@app.post("/approvals")
def create_approval(request: ApprovalRequest) -> ApprovalRequest:
    app.state.approvals[request.id] = request
    return request


@app.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, decision: Decision) -> ApprovalRequest:
    req = app.state.approvals.get(approval_id)
    if req is None:
        raise HTTPException(status_code=404, detail="approval not found")
    updated = req.model_copy(update={"status": decision.decision, "decided_by": decision.decided_by})
    app.state.approvals[approval_id] = updated
    return updated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/governance/tests/test_governance_api.py -v`
Expected: PASS (5 passed).

Note: `_init_state()` runs at import against real files (`data/`, `policies/rbac_policy.yaml`).
The policy file exists (Task 2). `data/` dirs are created by the file adapters. The test overrides
`app.state.*` with in-memory versions, so it does not depend on the file contents.

- [ ] **Step 5: Run full suite + lint**

Run: `uv run pytest` then `uv run ruff check .`
Expected: all pass; ruff clean (apply UP017 autofix if it fires).

- [ ] **Step 6: Commit**

```bash
git add services/governance/app.py services/governance/tests/test_governance_api.py
git commit -m "feat: add governance REST API (audit, playbooks, rbac, approvals)"
```

---

### Task 6: RCA context provider

**Files:**
- Create: `services/rca/__init__.py` (if missing — exists from Slice 0, leave as-is if present)
- Create: `services/rca/adapters/__init__.py`, `services/rca/adapters/context_provider.py`
- Test: `services/rca/tests/__init__.py`, `services/rca/tests/test_context_provider.py`

**Interfaces:**
- Consumes: `common.interfaces.ContextProvider`.
- Produces:
  - `NullContextProvider()` — returns empty context; satisfies `ContextProvider`.
  - `FileContextProvider(path: str)` — reads `deploys.json`, `topology.json`, `config_changes.json` from `path` (missing files → empty); `recent_deploys()`, `topology_for(labels)` (returns the whole topology dict; filtering by labels is a later concern — for now returns topology as-is), `config_changes()`. Satisfies `ContextProvider`.

- [ ] **Step 1: Write the failing test**

`services/rca/tests/__init__.py`: (empty file)

`services/rca/tests/test_context_provider.py`:

```python
import json

from common.interfaces import ContextProvider
from services.rca.adapters.context_provider import (
    FileContextProvider,
    NullContextProvider,
)


def test_null_provider_satisfies_protocol_and_is_empty():
    p = NullContextProvider()
    assert isinstance(p, ContextProvider)
    assert p.recent_deploys() == []
    assert p.topology_for({"service": "web"}) == {}
    assert p.config_changes() == []


def test_file_provider_reads_json(tmp_path):
    (tmp_path / "deploys.json").write_text(json.dumps(
        [{"service": "web", "version": "v2", "ts": "2026-08-13T00:00:00+00:00"}]))
    (tmp_path / "topology.json").write_text(json.dumps({"web": ["db", "cache"]}))
    (tmp_path / "config_changes.json").write_text(json.dumps(
        [{"key": "web.replicas", "ts": "2026-08-13T00:00:00+00:00"}]))
    p = FileContextProvider(str(tmp_path))
    assert p.recent_deploys()[0]["service"] == "web"
    assert p.topology_for({"service": "web"}) == {"web": ["db", "cache"]}
    assert p.config_changes()[0]["key"] == "web.replicas"


def test_file_provider_missing_files_are_empty(tmp_path):
    p = FileContextProvider(str(tmp_path))  # empty dir
    assert p.recent_deploys() == []
    assert p.topology_for({}) == {}
    assert p.config_changes() == []


def test_file_provider_satisfies_protocol(tmp_path):
    assert isinstance(FileContextProvider(str(tmp_path)), ContextProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/rca/tests/test_context_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.rca.adapters'`.

- [ ] **Step 3: Write the context providers**

`services/rca/adapters/__init__.py`:

```python
"""RCA adapters: concrete ContextProvider implementations."""
```

`services/rca/adapters/context_provider.py`:

```python
"""ContextProvider implementations for RCA enrichment.

NullContextProvider is the safe default and test double. FileContextProvider
reads static JSON (deploys/topology/config) — a stand-in for real Prometheus /
CMDB / git integrations, which slot in behind the same protocol later.
"""

from __future__ import annotations

import json
import os


class NullContextProvider:
    def recent_deploys(self) -> list[dict]:
        return []

    def topology_for(self, labels: dict[str, str]) -> dict:
        return {}

    def config_changes(self) -> list[dict]:
        return []


class FileContextProvider:
    def __init__(self, path: str) -> None:
        self._path = path

    def _read(self, filename: str, default):
        full = os.path.join(self._path, filename)
        if not os.path.exists(full):
            return default
        with open(full, encoding="utf-8") as fh:
            return json.load(fh)

    def recent_deploys(self) -> list[dict]:
        return self._read("deploys.json", [])

    def topology_for(self, labels: dict[str, str]) -> dict:
        return self._read("topology.json", {})

    def config_changes(self) -> list[dict]:
        return self._read("config_changes.json", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/rca/tests/test_context_provider.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add services/rca/adapters/ services/rca/tests/
git commit -m "feat: add RCA context providers (file + null)"
```

---

### Task 7: RCA enrich + rank + surface_runbook

**Files:**
- Create: `services/rca/enrich.py`
- Create: `services/rca/rank.py`
- Test: `services/rca/tests/test_enrich.py`, `services/rca/tests/test_rank.py`

**Interfaces:**
- Consumes: `common.contracts` (`Situation`, `EnrichmentContext`, `RootCauseHypothesis`, `Playbook`); `common.interfaces` (`ContextProvider`, `PlaybookStore`).
- Produces:
  - `enrich(situation: Situation, provider: ContextProvider) -> EnrichmentContext`.
  - `rank_hypotheses(situation: Situation, context: EnrichmentContext) -> list[RootCauseHypothesis]` — deterministic rules, sorted by confidence desc, at least one hypothesis always returned.
  - `surface_runbook(hypotheses: list[RootCauseHypothesis], store: PlaybookStore) -> Playbook | None` — looks up the top hypothesis's `suggested_runbook_id`.

- [ ] **Step 1: Write the failing test**

`services/rca/tests/test_enrich.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    EnrichmentContext,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.rca.enrich import enrich

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class FakeProvider:
    def recent_deploys(self):
        return [{"service": "web", "version": "v2", "ts": NOW.isoformat()}]

    def topology_for(self, labels):
        return {"web": ["db"]}

    def config_changes(self):
        return [{"key": "web.replicas", "ts": NOW.isoformat()}]


def _situation():
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name="cpu", value=99.0,
            labels={"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


def test_enrich_gathers_all_context():
    ctx = enrich(_situation(), FakeProvider())
    assert isinstance(ctx, EnrichmentContext)
    assert ctx.recent_deploys[0]["service"] == "web"
    assert ctx.topology == {"web": ["db"]}
    assert ctx.config_changes[0]["key"] == "web.replicas"
```

`services/rca/tests/test_rank.py`:

```python
from datetime import UTC, datetime

from common.contracts import (
    EnrichmentContext,
    HitlMode,
    Playbook,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.rca.adapters.context_provider import NullContextProvider
from services.rca.enrich import enrich
from services.rca.rank import rank_hypotheses, surface_runbook

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation(name="cpu", labels=None):
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name=name, value=99.0,
            labels=labels or {"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


def test_recent_deploy_ranks_first():
    ctx = EnrichmentContext(recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}])
    hyps = rank_hypotheses(_situation(labels={"service": "web"}), ctx)
    assert hyps[0].suggested_runbook_id == "rollback-deploy"
    assert hyps[0].confidence >= 0.7
    assert "web" in hyps[0].description or "deploy" in hyps[0].description.lower()


def test_resource_exhaustion_when_no_deploy():
    ctx = EnrichmentContext()  # no deploys
    hyps = rank_hypotheses(_situation(name="cpu_usage"), ctx)
    assert hyps[0].suggested_runbook_id == "scale-service"


def test_error_spike_for_log_events():
    ctx = EnrichmentContext()
    sit = _situation(name="error_rate")
    sit.member_events[0].kind = TelemetryKind.LOG
    hyps = rank_hypotheses(sit, ctx)
    assert any(h.suggested_runbook_id == "restart-pod" for h in hyps)


def test_fallback_hypothesis_when_nothing_matches():
    ctx = EnrichmentContext()
    hyps = rank_hypotheses(_situation(name="latency_p99"), ctx)
    assert len(hyps) >= 1
    assert hyps[-1].confidence <= 0.3  # the fallback is low-confidence


def test_hypotheses_sorted_by_confidence_desc():
    ctx = EnrichmentContext(recent_deploys=[{"service": "web", "version": "v2", "ts": NOW.isoformat()}])
    hyps = rank_hypotheses(_situation(name="cpu", labels={"service": "web"}), ctx)
    confidences = [h.confidence for h in hyps]
    assert confidences == sorted(confidences, reverse=True)


def test_surface_runbook_looks_up_top_hypothesis():
    from services.rca.adapters.context_provider import NullContextProvider  # noqa: F401

    class Store:
        def register(self, playbook): ...
        def get(self, playbook_id):
            if playbook_id == "scale-service":
                return Playbook(id="scale-service", name="Scale", match_rule="x",
                                steps=["s"], hitl_mode=HitlMode.HITL)
            return None
        def list(self): return []

    ctx = EnrichmentContext()
    hyps = rank_hypotheses(_situation(name="cpu_usage"), ctx)
    pb = surface_runbook(hyps, Store())
    assert pb is not None
    assert pb.id == "scale-service"


def test_enrich_null_provider_gives_empty_then_fallback():
    ctx = enrich(_situation(name="latency_p99"), NullContextProvider())
    hyps = rank_hypotheses(_situation(name="latency_p99"), ctx)
    assert hyps  # never empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest services/rca/tests/test_enrich.py services/rca/tests/test_rank.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.rca.enrich'`.

- [ ] **Step 3: Write enrich**

`services/rca/enrich.py`:

```python
"""Enrich a Situation with change/deploy/topology context.

Context is what makes a root-cause suggestion credible rather than a guess
(see flow.md 5.3). Pure function of (situation, provider).
"""

from __future__ import annotations

from common.contracts import EnrichmentContext, Situation
from common.interfaces import ContextProvider


def _merged_labels(situation: Situation) -> dict[str, str]:
    labels: dict[str, str] = {}
    for event in situation.member_events:
        labels.update(event.labels)
    return labels


def enrich(situation: Situation, provider: ContextProvider) -> EnrichmentContext:
    return EnrichmentContext(
        recent_deploys=provider.recent_deploys(),
        topology=provider.topology_for(_merged_labels(situation)),
        config_changes=provider.config_changes(),
    )
```

- [ ] **Step 4: Write rank**

`services/rca/rank.py`:

```python
"""Rank root-cause hypotheses with deterministic rules, and surface a runbook.

Each rule produces a scored RootCauseHypothesis when it fires; the list is
sorted best-first. A low-confidence fallback guarantees a non-empty result so
downstream always has something to act on (see flow.md 5.3).
"""

from __future__ import annotations

from common.contracts import (
    EnrichmentContext,
    Playbook,
    RootCauseHypothesis,
    Situation,
)
from common.interfaces import PlaybookStore

_SATURATION_TOKENS = ("cpu", "mem", "memory", "disk", "saturation")


def _service_labels(situation: Situation) -> set[str]:
    services: set[str] = set()
    for event in situation.member_events:
        svc = event.labels.get("service")
        if svc:
            services.add(svc)
    return services


def rank_hypotheses(
    situation: Situation, context: EnrichmentContext
) -> list[RootCauseHypothesis]:
    hypotheses: list[RootCauseHypothesis] = []
    services = _service_labels(situation)

    # Rule: a recent deploy touching one of the situation's services.
    deploy_hit = next(
        (d for d in context.recent_deploys if d.get("service") in services), None
    )
    if deploy_hit is not None:
        hypotheses.append(RootCauseHypothesis(
            situation_id=situation.id,
            description=f"recent deployment of {deploy_hit.get('service')} "
                        f"({deploy_hit.get('version')}) preceded the incident",
            confidence=0.8,
            evidence=[f"deploy {deploy_hit.get('service')}@{deploy_hit.get('version')}"],
            suggested_runbook_id="rollback-deploy",
        ))

    # Rule: resource-saturation metric names.
    names = " ".join(e.name.lower() for e in situation.member_events)
    if any(tok in names for tok in _SATURATION_TOKENS):
        hypotheses.append(RootCauseHypothesis(
            situation_id=situation.id,
            description="resource saturation across the affected service",
            confidence=0.6,
            evidence=[f"metrics: {names}"],
            suggested_runbook_id="scale-service",
        ))

    # Rule: log/error events.
    if any(e.kind.value in ("log",) or "error" in e.name.lower()
           for e in situation.member_events):
        hypotheses.append(RootCauseHypothesis(
            situation_id=situation.id,
            description="error spike in service logs",
            confidence=0.5,
            evidence=["log/error events present"],
            suggested_runbook_id="restart-pod",
        ))

    # Fallback: always give downstream something.
    if not hypotheses:
        hypotheses.append(RootCauseHypothesis(
            situation_id=situation.id,
            description="root cause undetermined from available signals",
            confidence=0.2,
            evidence=[],
            suggested_runbook_id=None,
        ))

    hypotheses.sort(key=lambda h: h.confidence, reverse=True)
    return hypotheses


def surface_runbook(
    hypotheses: list[RootCauseHypothesis], store: PlaybookStore
) -> Playbook | None:
    if not hypotheses:
        return None
    runbook_id = hypotheses[0].suggested_runbook_id
    if runbook_id is None:
        return None
    return store.get(runbook_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest services/rca/tests/test_enrich.py services/rca/tests/test_rank.py -v`
Expected: PASS (1 + 7 = 8 passed).

- [ ] **Step 6: Commit**

```bash
git add services/rca/enrich.py services/rca/rank.py services/rca/tests/test_enrich.py services/rca/tests/test_rank.py
git commit -m "feat: add RCA enrichment and hypothesis ranking"
```

---

### Task 8: RCA consumer + lifespan

**Files:**
- Create: `services/rca/consumer.py`
- Modify: `services/rca/app.py`
- Test: `services/rca/tests/test_consumer.py`

**Interfaces:**
- Consumes: `common.envelope` (`iter_models`, `publish_model`); `common.contracts` (`Situation`, `SituationStatus`, `DiagnosedSituation`, `AuditRecord`); `enrich`, `rank_hypotheses`, `surface_runbook`; `ContextProvider`, `PlaybookStore`, `AuditSink`; `services.base.create_app`.
- Produces:
  - `diagnose(situation, provider, store) -> DiagnosedSituation` — enrich → rank → surface → build a `DiagnosedSituation` with `situation.status = DIAGNOSED`.
  - `run_consumer(bus, provider, store, audit_sink, stop_event) -> None` — consumes `situations.detected`, diagnoses each, publishes `DiagnosedSituation` to `situations.diagnosed`, writes an `AuditRecord` (actor `"rca-service"`, action `"diagnose"`, resource `f"situation:{id}"`, decision `"allow"`, `correlation_id=id`). Breaks on `stop_event`.
  - `services/rca/app.py` starts `run_consumer` in a daemon thread via FastAPI lifespan.

- [ ] **Step 1: Write the failing test**

`services/rca/tests/test_consumer.py`:

```python
import threading
from datetime import UTC, datetime

from common.contracts import (
    DiagnosedSituation,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from common.envelope import decode_model
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.adapters.context_provider import NullContextProvider
from services.rca.consumer import diagnose, run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation(name="cpu_usage", labels=None):
    return Situation(
        id="sit-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name=name, value=99.0,
            labels=labels or {"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig",
    )


class ScriptedBus:
    def __init__(self, script):
        self._script = script
        self.published = []

    def publish(self, topic, message):
        self.published.append((topic, message))

    def consume(self, topic, group):
        yield from self._script


def test_diagnose_sets_status_and_hypotheses():
    d = diagnose(_situation(), NullContextProvider(), InMemoryPlaybookStore())
    assert isinstance(d, DiagnosedSituation)
    assert d.situation.status == SituationStatus.DIAGNOSED
    assert len(d.hypotheses) >= 1
    assert d.suggested_runbook_id == "scale-service"  # cpu_usage → resource-exhaustion


def test_consumer_publishes_diagnosed_and_audits():
    sit = _situation()
    bus = ScriptedBus([{"data": sit.model_dump_json()}])
    audit = InMemoryAuditSink()
    run_consumer(bus, NullContextProvider(), InMemoryPlaybookStore(), audit, threading.Event())

    diagnosed = [m for (t, m) in bus.published if t == "situations.diagnosed"]
    assert len(diagnosed) == 1
    d = decode_model(diagnosed[0], DiagnosedSituation)
    assert d.situation.id == "sit-1"
    assert d.situation.status == SituationStatus.DIAGNOSED
    # audit record written, threaded by correlation_id == situation id
    records = audit.records()
    assert len(records) == 1
    assert records[0].action == "diagnose"
    assert records[0].correlation_id == "sit-1"


def test_consumer_stops_on_stop_event():
    def infinite():
        while True:
            yield {"data": _situation().model_dump_json()}

    class InfBus(ScriptedBus):
        def consume(self, topic, group):
            return infinite()

    bus = InfBus([])
    stop = threading.Event()
    stop.set()
    run_consumer(bus, NullContextProvider(), InMemoryPlaybookStore(),
                 InMemoryAuditSink(), stop)
    assert bus.published == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest services/rca/tests/test_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.rca.consumer'`.

- [ ] **Step 3: Write the consumer**

`services/rca/consumer.py`:

```python
"""Bus consumer for rca-service.

Consumes situations.detected, diagnoses each (enrich → rank → surface runbook),
marks it diagnosed, publishes a DiagnosedSituation to situations.diagnosed, and
writes an audit record threaded by the situation id. Runs in a daemon thread
started by the FastAPI lifespan; a stop_event allows clean shutdown.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from common.contracts import (
    AuditRecord,
    DiagnosedSituation,
    Situation,
    SituationStatus,
)
from common.envelope import iter_models, publish_model
from common.interfaces import AuditSink, ContextProvider, PlaybookStore
from services.rca.enrich import enrich
from services.rca.rank import rank_hypotheses, surface_runbook


def diagnose(
    situation: Situation, provider: ContextProvider, store: PlaybookStore
) -> DiagnosedSituation:
    context = enrich(situation, provider)
    hypotheses = rank_hypotheses(situation, context)
    runbook = surface_runbook(hypotheses, store)
    diagnosed_situation = situation.model_copy(update={"status": SituationStatus.DIAGNOSED})
    return DiagnosedSituation(
        situation=diagnosed_situation,
        hypotheses=hypotheses,
        suggested_runbook_id=runbook.id if runbook is not None else hypotheses[0].suggested_runbook_id,
    )


def run_consumer(
    bus,
    provider: ContextProvider,
    store: PlaybookStore,
    audit_sink: AuditSink,
    stop_event: threading.Event,
) -> None:
    for situation in iter_models(bus, "situations.detected", "rca", Situation):
        if stop_event.is_set():
            break
        diagnosed = diagnose(situation, provider, store)
        publish_model(bus, "situations.diagnosed", diagnosed)
        audit_sink.write(AuditRecord(
            actor="rca-service",
            action="diagnose",
            resource=f"situation:{situation.id}",
            decision="allow",
            ts=datetime.now(UTC),
            correlation_id=situation.id,
        ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest services/rca/tests/test_consumer.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire the lifespan in `services/rca/app.py`**

Replace `services/rca/app.py` with:

```python
"""RCA service: enrich a Situation and rank root-cause hypotheses."""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import get_settings
from services.base import create_app
from services.governance.adapters.audit_sink import FileAuditSink
from services.governance.adapters.playbook_store import FilePlaybookStore
from services.rca.adapters.context_provider import FileContextProvider
from services.rca.consumer import run_consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    stop_event = threading.Event()
    provider = FileContextProvider(settings.rca_context_path)
    store = FilePlaybookStore(settings.playbook_store_path)
    audit_sink = FileAuditSink(settings.audit_store_path)
    thread = threading.Thread(
        target=run_consumer,
        args=(app.state.bus, provider, store, audit_sink, stop_event),
        daemon=True,
    )
    thread.start()
    app.state.consumer_stop = stop_event
    app.state.consumer_thread = thread
    try:
        yield
    finally:
        stop_event.set()


app = create_app("rca-service")
app.router.lifespan_context = lifespan
```

- [ ] **Step 6: Run the rca tests + health**

Run: `uv run pytest services/rca/ -v`
Expected: PASS. `/health` for rca-service still returns `{"service": "rca-service", "status": "ok"}` (covered by the existing `tests/test_services.py`).

- [ ] **Step 7: Commit**

```bash
git add services/rca/consumer.py services/rca/app.py services/rca/tests/test_consumer.py
git commit -m "feat: wire RCA consumer thread via FastAPI lifespan"
```

---

### Task 9: End-to-end acceptance + docs + gitignore

**Files:**
- Create: `tests/test_slice2_acceptance.py`
- Modify: `.gitignore` (ensure `data/` is ignored — check; it already contains `data/` from Slice 0, confirm and leave)
- Modify: `README.md` (roadmap: Slice 2 → done; add a Quickstart note line)

**Interfaces:**
- Consumes: everything above.
- Produces: an in-process end-to-end test proving `situations.detected` → rca → `situations.diagnosed` with the right top hypothesis and an audit record.

- [ ] **Step 1: Write the acceptance test**

`tests/test_slice2_acceptance.py`:

```python
"""Slice-2 acceptance: a detected Situation is diagnosed end-to-end in-process."""

import threading
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
from common.envelope import decode_model, publish_model
from services.governance.adapters.audit_sink import InMemoryAuditSink
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.consumer import run_consumer

NOW = datetime(2026, 8, 13, tzinfo=UTC)


class InMemoryBus:
    def __init__(self):
        self.topics: dict[str, list[dict]] = {}

    def publish(self, topic, message):
        self.topics.setdefault(topic, []).append(message)

    def consume(self, topic, group):
        yield from list(self.topics.get(topic, []))


class DeployProvider:
    """Context provider that reports a recent deploy of 'web'."""

    def recent_deploys(self):
        return [{"service": "web", "version": "v2", "ts": NOW.isoformat()}]

    def topology_for(self, labels):
        return {"web": ["db"]}

    def config_changes(self):
        return []


def test_detected_situation_is_diagnosed_with_recent_deploy_hypothesis():
    bus = InMemoryBus()
    audit = InMemoryAuditSink()
    store = InMemoryPlaybookStore()
    store.register(Playbook(id="rollback-deploy", name="Rollback Deployment",
                            match_rule="x", steps=["kubectl rollout undo deploy/web"],
                            hitl_mode=HitlMode.HITL, reversible=True,
                            rollback_steps=[]))

    # A detected Situation on the 'web' service.
    situation = Situation(
        id="sit-web-1", status=SituationStatus.DETECTED,
        member_events=[TelemetryEvent(
            source="prom", kind=TelemetryKind.METRIC, name="cpu_usage", value=99.0,
            labels={"service": "web"}, ts=NOW, fingerprint="fp",
        )],
        severity="high", first_seen=NOW, last_seen=NOW, signature="sig-web",
    )
    publish_model(bus, "situations.detected", situation)

    # Run RCA against a provider that knows about the recent 'web' deploy.
    run_consumer(bus, DeployProvider(), store, audit, threading.Event())

    # Exactly one DiagnosedSituation, top hypothesis = recent deploy → rollback.
    diagnosed_msgs = bus.topics.get("situations.diagnosed", [])
    assert len(diagnosed_msgs) == 1
    d = decode_model(diagnosed_msgs[0], DiagnosedSituation)
    assert d.situation.status == SituationStatus.DIAGNOSED
    assert d.hypotheses[0].suggested_runbook_id == "rollback-deploy"
    assert d.suggested_runbook_id == "rollback-deploy"

    # Audit trail recorded, threaded by the situation id.
    records = audit.records()
    assert len(records) == 1
    assert records[0].action == "diagnose"
    assert records[0].correlation_id == "sit-web-1"
```

- [ ] **Step 2: Run the acceptance test**

Run: `uv run pytest tests/test_slice2_acceptance.py -v`
Expected: PASS (1 passed).

- [ ] **Step 3: Confirm `data/` is gitignored**

Run: `git check-ignore data/ && echo IGNORED || echo NOT-IGNORED`
Expected: `IGNORED` (Slice 0's `.gitignore` has `data/`). If `NOT-IGNORED`, add `data/` to `.gitignore`.

- [ ] **Step 4: Run the full suite + lint**

Run: `uv run pytest` then `uv run ruff check .`
Expected: all pass; ruff clean (apply UP017 autofix if it fires — it changes only tzinfo tokens, never logic).

- [ ] **Step 5: Update the README roadmap**

In `README.md`, change the Slice 2 roadmap row status from `⏳ planned` to `✅ done` (ONLY the
Slice 2 row; Slices 3-4 stay `⏳ planned`). Under Quickstart, add this line after the Slice-1 line:

```
> Slice 2 adds rca-service (diagnoses Situations → `situations.diagnosed`) and governance-service (audit log, playbook registry, RBAC at 8005).
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_slice2_acceptance.py README.md .gitignore
git commit -m "test: add slice-2 end-to-end acceptance; mark slice done"
```

---

## Self-Review

**1. Spec coverage** (against the Slice-2 spec §2–4, §8):
- Contracts `DiagnosedSituation` + `EnrichmentContext` (§2.1) → Task 1 ✓
- Interfaces `PlaybookStore` + `ContextProvider` (§2.2) → Task 1 ✓
- Config additions (§2.3) → Task 1 ✓
- RBAC policy + `/rbac/check` (§3.1, §3.3) → Tasks 2, 5 ✓
- Audit sinks + `/audit` (§3.2, §3.3) → Tasks 3, 5 ✓
- Playbook store + `/playbooks` + seeds (§3.2, §3.3) → Tasks 4, 5 ✓
- Approval endpoints (§3.3) → Task 5 ✓
- ContextProvider File/Null (§4.1) → Task 6 ✓
- enrich + rank + surface_runbook (§4.2, §4.3) → Task 7 ✓
- Consumer + lifespan + audit write (§4.4) → Task 8 ✓
- End-to-end acceptance (§6) → Task 9 ✓
- *Deferred by design (not gaps):* Postgres adapters, real context integrations, RBAC enforcement, approval callers — all Slice 3+/later per §10.

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code and test step is complete literal content.

**3. Type consistency:**
- `EnrichmentContext(recent_deploys, topology, config_changes)` / `DiagnosedSituation(situation, hypotheses, suggested_runbook_id)` — Task 1, used in 7/8/9. ✓
- `PlaybookStore.register/get/list`, `ContextProvider.recent_deploys/topology_for/config_changes` — Task 1, implemented in 4/6, consumed in 7/8. ✓
- `RbacPolicy(roles, actors)` + `.check(actor, action, resource)` + `.from_file(path)` — Task 2, used in 5. ✓
- `InMemoryAuditSink`/`FileAuditSink` with `write`/`records` — Task 3, used in 5/8/9. ✓
- `InMemoryPlaybookStore`/`FilePlaybookStore` + `load_seed_playbooks` — Task 4, used in 5/7/8/9. ✓
- `enrich(situation, provider)`, `rank_hypotheses(situation, context)`, `surface_runbook(hypotheses, store)` — Task 7, used in 8. ✓
- `diagnose(situation, provider, store)`, `run_consumer(bus, provider, store, audit_sink, stop_event)` — Task 8, used in 9. ✓
- runbook ids consistent: `rollback-deploy`/`scale-service`/`restart-pod` across ranking rules (Task 7), seed playbooks (Task 4), and acceptance (Task 9). ✓
- `situations.detected` (consumed) / `situations.diagnosed` (published) topics consistent across Tasks 8, 9. ✓

One thing verified: Task 8's `diagnose` sets `suggested_runbook_id` from the surfaced runbook if present, else the top hypothesis's id — so the acceptance test (Task 9), which registers the `rollback-deploy` playbook, gets `suggested_runbook_id == "rollback-deploy"` via the surfaced runbook. And `test_diagnose_sets_status_and_hypotheses` (Task 8) uses an EMPTY playbook store, so `surface_runbook` returns None and `suggested_runbook_id` falls back to the top hypothesis's id (`scale-service` for `cpu_usage`). Both paths consistent.
