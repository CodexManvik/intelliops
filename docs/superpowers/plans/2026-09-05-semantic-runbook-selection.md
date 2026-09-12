# Semantic runbook selection (embedding similarity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add semantic runbook selection that AUGMENTS the existing keyword rules — when no rule fires confidently, embed the incident's symptoms and rank the human-vetted playbooks by cosine similarity, picking the best match above a threshold (else the gap → the AI-authoring flow). The selector can only ever rank REGISTERED playbooks; it never fabricates an id or an action. Default OFF (a NullRunbookSelector = today's behavior exactly).

**Architecture:** A curated `symptoms` field on `Playbook` is the match target. A `RunbookSelector` interface has two config-switched adapters: `NullRunbookSelector` (off-default, yields today's behavior) and `EmbeddingRunbookSelector` (local `sentence-transformers` all-MiniLM-L6-v2, lazy-loaded, cosine similarity via numpy, fail-safe). A `select_runbook` helper in `rca/rank.py` runs the rules first (`surface_runbook`), then the selector as a fallback, returning (playbook, score, source∈{rule,semantic,none}). `diagnose()` records the score+source on a semantic match. `sentence-transformers` goes in the `ml` extra only (slim-boundary).

**Tech Stack:** Python 3.11/3.12, Pydantic v2, sentence-transformers (all-MiniLM-L6-v2), numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-09-05-semantic-runbook-selection-design.md` (read alongside this plan). Standalone follow-on to PRs #33/#34/#35 (all merged).

## Global Constraints

- **Branch `feat/semantic-runbook-selection` off current master.**
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~482 base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean (only if the optional UI chip is done).
- **Env:** `uv sync --extra ml --extra k8s` once (a bare `uv sync` fails collection — missing river/kubernetes; and after Task 3 you need the ml extra for sentence-transformers).
- **Safety invariant (must hold every task):** the selector returns ONLY an id of a playbook present in `store`, or None — never a fabricated id, never an action. `runbook_selector_mode` defaults `"off"` → `NullRunbookSelector` → `diagnose` reproduces today's `surface_runbook` behavior EXACTLY. The keyword rules in `rank_hypotheses` are NOT removed.
- **Slim-boundary (from PR #29):** `sentence-transformers` in the `ml` optional-dependency group ONLY. It is imported ONLY inside `EmbeddingRunbookSelector` (lazy, inside the method/constructor), which lives in RCA. The CI slim-boundary check (action/governance/feedback importable WITHOUT the ml extra) must stay green — do NOT add a top-level `import sentence_transformers` anywhere, and do NOT import the selector adapter from a module that action/governance/feedback load.
- **Fail-safe:** `EmbeddingRunbookSelector.select` returns None on ANY internal error (model load, encode, etc.) and never raises out of `diagnose`. A missing/empty `symptoms` playbook is skipped, not an error.
- **Additive contract:** `symptoms` is optional, defaults None; existing playbooks/tests unchanged.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** push; open a PR against master; the USER merges. Never merge to master.

---

## File Structure

- `common/contracts.py` — add `symptoms: str | None = None` to `Playbook`.
- `common/interfaces.py` — add the `RunbookSelector` protocol.
- `common/config.py` — add `runbook_selector_mode` + `runbook_selector_model` + `runbook_selector_threshold`.
- `playbooks/{scale-service,restart-pod,rollback-deploy}.yaml` — add `symptoms:` text.
- `services/rca/adapters/runbook_selector.py` (new) — `NullRunbookSelector` + `EmbeddingRunbookSelector`.
- `services/rca/rank.py` — add the `select_runbook` helper (rules-first, then selector).
- `services/rca/consumer.py` — `diagnose`/`run_consumer` gain a `selector` param; record score+source on a semantic match.
- `services/rca/app.py` — `_make_runbook_selector` factory + thread it into `run_consumer`.
- `pyproject.toml` — add `sentence-transformers>=3.0` to the `ml` extra.
- `deploy/k8s/README.md` (or an RCA README section) — how selection works + how to enable.
- Tests (flat `tests/` + `services/rca/tests/` — check which exists; RCA has per-service tests): contract test, the `select_runbook` augment-logic test (fake selector), the `EmbeddingRunbookSelector` fail-safe + closed-catalog + deterministic-cosine test.

---

## Task 1: Contract + seed symptoms + protocol + config

**Files:**
- Modify: `common/contracts.py` (`Playbook` — add `symptoms`)
- Modify: `common/interfaces.py` (add `RunbookSelector` protocol)
- Modify: `common/config.py` (add the 3 selector fields)
- Modify: `playbooks/scale-service.yaml`, `playbooks/restart-pod.yaml`, `playbooks/rollback-deploy.yaml` (add `symptoms:`)
- Test: `tests/test_semantic_selection_contracts.py` (new)

**Interfaces:**
- Produces: `Playbook.symptoms: str | None = None`; `common.interfaces.RunbookSelector` protocol with `select(self, situation: Situation, hypothesis: RootCauseHypothesis, store: PlaybookStore) -> tuple[str, float] | None`; `Settings.runbook_selector_mode="off"` + `runbook_selector_model="all-MiniLM-L6-v2"` + `runbook_selector_threshold=0.45`.

**Note:** `common/interfaces.py` already imports `Situation`, `PlaybookStore` and `RootCauseHypothesis` from `common.contracts` (verify — RootCauseHypothesis is used by other protocols). Add any missing import.

- [ ] **Step 1: Write the failing test**

Create `tests/test_semantic_selection_contracts.py`:

```python
from common.contracts import HitlMode, Playbook, RemediationStep


def test_playbook_symptoms_optional_and_additive():
    pb = Playbook(id="p", name="n", match_rule="*", steps=[RemediationStep(action="restart")],
                  hitl_mode=HitlMode.HITL)
    assert pb.symptoms is None  # default
    pb2 = Playbook(id="p2", name="n", match_rule="*", steps=[], hitl_mode=HitlMode.HITL,
                   symptoms="high CPU, saturation")
    assert pb2.symptoms == "high CPU, saturation"


def test_seed_playbooks_have_symptoms():
    # the 3 seed playbooks load with non-empty symptom text
    from services.governance.adapters.playbook_store import load_seed_playbooks
    pbs = {p.id: p for p in load_seed_playbooks()}
    for pid in ("scale-service", "restart-pod", "rollback-deploy"):
        assert pbs[pid].symptoms and len(pbs[pid].symptoms) > 10
```

(If `load_seed_playbooks` has a different name/location, adjust — find it with `grep -rn "def load_seed_playbooks\|seed" services/governance/adapters/playbook_store.py`. If seed loading works differently, assert the symptom text by reading the YAML directly instead.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_semantic_selection_contracts.py -v`
Expected: FAIL — no `symptoms` field / seed playbooks lack it.

- [ ] **Step 3: Add `symptoms` to `Playbook`**

In `common/contracts.py`, add to the `Playbook` class (after `rollback_steps`):
```python
    symptoms: str | None = None  # human-written "when this applies" — the semantic match target
```

- [ ] **Step 4: Add symptom text to the 3 seed playbooks**

Append a `symptoms:` line to each YAML:
- `playbooks/scale-service.yaml`: `symptoms: "high CPU or memory saturation, resource exhaustion, service thrashing under sustained load, OOM kills, throttling"`
- `playbooks/restart-pod.yaml`: `symptoms: "error spikes in logs, crash loops, wedged or hung process, elevated 5xx errors, stuck workers"`
- `playbooks/rollback-deploy.yaml`: `symptoms: "regression immediately following a recent deployment or release, new version misbehaving, errors starting right after a rollout"`

(Match the existing YAML style in those files — confirm they're flat key: value.)

- [ ] **Step 5: Add the `RunbookSelector` protocol**

In `common/interfaces.py`, add `RootCauseHypothesis` to the `common.contracts` import if not present, then:
```python
@runtime_checkable
class RunbookSelector(Protocol):
    """Selects a runbook for a situation by semantic similarity among the
    registered playbooks. Returns (playbook_id, score) or None. Ranks only
    existing playbooks — never fabricates an id. Never raises."""

    def select(
        self, situation: Situation, hypothesis: RootCauseHypothesis, store: PlaybookStore
    ) -> tuple[str, float] | None: ...
```

- [ ] **Step 6: Add config fields**

In `common/config.py`, after the runbook-author block (or near the llm settings):
```python
    runbook_selector_mode: str = "off"  # "off" | "embedding"
    runbook_selector_model: str = "all-MiniLM-L6-v2"
    runbook_selector_threshold: float = 0.45  # min cosine similarity to accept a match
```

- [ ] **Step 7: Run tests + full suite + lint**

Run: `uv run pytest tests/test_semantic_selection_contracts.py -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: new tests pass; base suite green (existing playbook-loading tests still pass with the added field); lint clean.

- [ ] **Step 8: Commit**

```bash
git add common/contracts.py common/interfaces.py common/config.py playbooks/ tests/test_semantic_selection_contracts.py
git commit -m "feat(selection): symptoms field on Playbook, RunbookSelector protocol, selector config

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: The select_runbook augment-logic + NullRunbookSelector + wiring

**Files:**
- Create: `services/rca/adapters/runbook_selector.py` (`NullRunbookSelector` only in this task)
- Modify: `services/rca/rank.py` (add `select_runbook` helper)
- Modify: `services/rca/consumer.py` (`diagnose`/`run_consumer` gain `selector`; record score+source)
- Modify: `services/rca/app.py` (`_make_runbook_selector` factory + wiring)
- Test: `services/rca/tests/test_runbook_selection.py` (new) — verify `services/rca/tests/` exists (RCA has per-service tests); if not, use `tests/test_runbook_selection.py`.

**Interfaces:**
- Consumes: `RunbookSelector` (Task 1), `Playbook`, `RootCauseHypothesis`, `surface_runbook`/`rank_hypotheses` (existing in rank.py), `PlaybookStore`.
- Produces:
  - `NullRunbookSelector.select(...) -> None`.
  - `rank.select_runbook(hypotheses, situation, store, selector) -> tuple[Playbook | None, float | None, str]` where the 3rd element is `"rule"` | `"semantic"` | `"none"`.
  - `diagnose(..., selector=NullRunbookSelector())` uses it; `run_consumer` threads `selector`; `_make_runbook_selector(settings) -> RunbookSelector`.

**Ruling on signature:** `selector` is added to `diagnose`/`run_consumer` after the existing `reliability_provider`-style params (keyword-or-trailing-positional, matching how `explainer`/`reliability_provider` are threaded). Default `NullRunbookSelector()` where a default is natural (diagnose), explicit in run_consumer/app.py.

- [ ] **Step 1: Write the failing tests**

Create the test (path per the check above). Use a deterministic FAKE selector — NO model needed:

```python
from datetime import UTC, datetime

from common.contracts import (
    HitlMode, Playbook, RemediationStep, RootCauseHypothesis, Situation, SituationStatus,
    TelemetryEvent, TelemetryKind,
)
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.adapters.runbook_selector import NullRunbookSelector
from services.rca.rank import rank_hypotheses, select_runbook

TS = datetime(2026, 9, 5, tzinfo=UTC)


def _situation(metric_name="cpu_usage"):
    ev = TelemetryEvent(source="p", kind=TelemetryKind.METRIC, name=metric_name, value=90.0,
                        labels={"service": "web"}, ts=TS, fingerprint="fp")
    return Situation(id="sit-1", status=SituationStatus.DETECTED, member_events=[ev],
                     severity="high", first_seen=TS, last_seen=TS, signature="sig-1")


def _store():
    s = InMemoryPlaybookStore()
    s.register(Playbook(id="scale-service", name="Scale", match_rule="*",
                        steps=[RemediationStep(action="scale", replicas=1)], hitl_mode=HitlMode.HITL,
                        symptoms="high CPU or memory saturation"))
    return s


class _FakeSelector:
    def __init__(self, result):  # (id, score) or None
        self._result = result
        self.called = False
    def select(self, situation, hypothesis, store):
        self.called = True
        return self._result


def test_rule_wins_and_selector_not_consulted():
    # cpu_usage triggers the saturation keyword rule -> scale-service
    sit = _situation("cpu_usage")
    hyps = rank_hypotheses(sit, __import__("common.contracts", fromlist=["EnrichmentContext"]).EnrichmentContext())
    selector = _FakeSelector(("should-not-be-used", 0.99))
    pb, score, source = select_runbook(hyps, sit, _store(), selector)
    assert source == "rule"
    assert pb.id == "scale-service"
    assert selector.called is False  # rule fired -> selector skipped


def test_semantic_fallback_when_no_rule_fires():
    # a metric with NO saturation/error/deploy signal -> no rule -> selector runs
    sit = _situation("weird_metric_xyz")
    from common.contracts import EnrichmentContext
    hyps = rank_hypotheses(sit, EnrichmentContext())
    selector = _FakeSelector(("scale-service", 0.88))
    pb, score, source = select_runbook(hyps, sit, _store(), selector)
    assert source == "semantic"
    assert pb.id == "scale-service" and score == 0.88
    assert selector.called is True


def test_no_rule_no_semantic_is_gap():
    sit = _situation("weird_metric_xyz")
    from common.contracts import EnrichmentContext
    hyps = rank_hypotheses(sit, EnrichmentContext())
    selector = _FakeSelector(None)  # selector finds nothing
    pb, score, source = select_runbook(hyps, sit, _store(), selector)
    assert source == "none"
    assert pb is None


def test_null_selector_reproduces_rule_only_behavior():
    sit = _situation("weird_metric_xyz")
    from common.contracts import EnrichmentContext
    hyps = rank_hypotheses(sit, EnrichmentContext())
    pb, score, source = select_runbook(hyps, sit, _store(), NullRunbookSelector())
    assert source == "none" and pb is None  # no rule + null selector = gap, exactly as today
```

(Verify `_SATURATION_TOKENS` really doesn't contain a token in `weird_metric_xyz` — it doesn't. Confirm the rule for a bare non-matching metric produces the fallback hypothesis with `suggested_runbook_id=None`, so `surface_runbook` returns None and the selector is consulted.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest <the test path> -v`
Expected: FAIL — `select_runbook` / `NullRunbookSelector` don't exist.

- [ ] **Step 3: Create `NullRunbookSelector`**

`services/rca/adapters/runbook_selector.py`:
```python
"""RunbookSelector implementations: pick a runbook by semantic similarity.

NullRunbookSelector is the CI-safe default (runbook_selector_mode="off"): it
selects nothing, so the runbook comes purely from the keyword rules (today's
behavior). EmbeddingRunbookSelector (added later) does the real semantic match."""

from __future__ import annotations

from common.contracts import RootCauseHypothesis, Situation
from common.interfaces import PlaybookStore


class NullRunbookSelector:
    def select(
        self, situation: Situation, hypothesis: RootCauseHypothesis, store: PlaybookStore
    ) -> tuple[str, float] | None:
        return None
```

- [ ] **Step 4: Add `select_runbook` to `rank.py`**

In `services/rca/rank.py`, add (imports: `Situation`, `PlaybookStore` are likely already imported; add if not):
```python
def select_runbook(
    hypotheses: list[RootCauseHypothesis],
    situation: Situation,
    store: PlaybookStore,
    selector,
) -> tuple[Playbook | None, float | None, str]:
    """Rules-first runbook selection, semantic fallback. Returns
    (playbook, score, source) with source in {"rule","semantic","none"}."""
    rule_runbook = surface_runbook(hypotheses, store)
    if rule_runbook is not None:
        return rule_runbook, None, "rule"
    if hypotheses:
        hit = selector.select(situation, hypotheses[0], store)
        if hit is not None:
            pid, score = hit
            pb = store.get(pid)
            if pb is not None:  # closed catalog: only a registered id is honored
                return pb, score, "semantic"
    return None, None, "none"
```

- [ ] **Step 5: Wire `selector` through `diagnose` + `consumer` + `app.py`**

In `services/rca/consumer.py`, `diagnose` gains `selector=None` (default to `NullRunbookSelector()` inside if None, or require it — match the `reliability_provider` pattern). Replace the `runbook = surface_runbook(hypotheses, store)` line with:
```python
    from services.rca.adapters.runbook_selector import NullRunbookSelector
    sel = selector or NullRunbookSelector()
    runbook, score, source = select_runbook(hypotheses, situation, store, sel)
```
And when `source == "semantic"`, append the provenance to the top hypothesis's evidence before the explanation step, e.g.:
```python
    if source == "semantic" and hypotheses and runbook is not None:
        top0 = hypotheses[0]
        hypotheses = [
            top0.model_copy(update={
                "suggested_runbook_id": runbook.id,
                "evidence": [*top0.evidence, f"semantic match: {runbook.id} ({score:.2f})"],
            }),
            *hypotheses[1:],
        ]
```
Keep the final `suggested_runbook_id=runbook.id if runbook is not None else hypotheses[0].suggested_runbook_id`. `run_consumer` threads `selector` (import `select_runbook`). `services/rca/app.py`: add `_make_runbook_selector(settings)` (embedding branch lazy-imports `EmbeddingRunbookSelector` — added in Task 3; default `NullRunbookSelector()`), thread into `run_consumer`'s args. Follow how `explainer`/`_make_*` are already wired in `rca/app.py`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest <the test path> -v`
Expected: PASS (all 4).

- [ ] **Step 7: Full suite — existing RCA tests must be unaffected**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. The existing `diagnose`/RCA acceptance tests must still pass — with the default (Null) selector, `select_runbook` returns exactly what `surface_runbook` did (rule or None). If any existing test calls `diagnose`/`run_consumer` with the old arity, add the `selector` arg (default Null) at that call site.

- [ ] **Step 8: Commit**

```bash
git add services/rca/adapters/runbook_selector.py services/rca/rank.py services/rca/consumer.py services/rca/app.py <the test path>
git commit -m "feat(selection): rules-first + semantic-fallback select_runbook; NullRunbookSelector

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: EmbeddingRunbookSelector (the real semantic match)

**Files:**
- Modify: `services/rca/adapters/runbook_selector.py` (add `EmbeddingRunbookSelector`)
- Modify: `pyproject.toml` (add `sentence-transformers>=3.0` to the `ml` extra)
- Test: extend the Task 2 test file (or a new `services/rca/tests/test_embedding_selector.py`)

**Interfaces:**
- Consumes: `RunbookSelector`, `Playbook`, `PlaybookStore`, numpy.
- Produces: `EmbeddingRunbookSelector(model_name="all-MiniLM-L6-v2", threshold=0.45)` implementing `select(...) -> tuple[str, float] | None`.

**Ruling on testing the model:** loading a real sentence-transformers model in CI is slow/heavy and may not be available offline. So: (a) the cosine/threshold/closed-catalog/fail-safe LOGIC is tested DETERMINISTICALLY by injecting a fake encode function (a stub that maps known strings to fixed vectors), NO real model; (b) ONE real-model integration test (the keyword-miss → scale-service case) is marked so CI without the model still passes (e.g. `@pytest.mark.skipif` on model-import failure, or gated behind an env flag). The default suite must stay green WITHOUT downloading a model. Cost if wrong: the real-model path is exercised only in the manual/opt-in run — acceptable, matches how the live k8s/LLM paths are handled in this repo.

- [ ] **Step 1: Write the failing tests (deterministic, fake encode)**

Extend the selector test file. Structure `EmbeddingRunbookSelector` so its encode step is injectable (a module-level `_load_model()` or an `encode_fn` param the test can stub). Example:

```python
import numpy as np
from services.rca.adapters import runbook_selector as rs


def _fake_encode(texts):
    # deterministic toy embeddings: "saturation-ish" -> [1,0]; "deploy-ish" -> [0,1]; else -> [0.5,0.5]
    out = []
    for t in texts:
        tl = t.lower()
        if any(w in tl for w in ("cpu", "memory", "saturation", "thrash", "load")):
            out.append([1.0, 0.0])
        elif any(w in tl for w in ("deploy", "release", "rollout")):
            out.append([0.0, 1.0])
        else:
            out.append([0.5, 0.5])
    return np.array(out)


def test_embedding_selector_matches_semantically_via_fake(monkeypatch):
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False)
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    # a "thrashing under load" situation (no literal saturation token) matches the scale-service symptoms
    sit = _situation("container_working_set_bytes")
    hyp = RootCauseHypothesis(situation_id="sit-1", description="service thrashing under load", confidence=0.2)
    hit = sel.select(sit, hyp, _store())
    assert hit is not None and hit[0] == "scale-service" and hit[1] >= 0.8


def test_embedding_selector_closed_catalog_empty_store(monkeypatch):
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False)
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    from services.governance.adapters.playbook_store import InMemoryPlaybookStore
    hyp = RootCauseHypothesis(situation_id="sit-1", description="anything", confidence=0.2)
    assert sel.select(_situation(), hyp, InMemoryPlaybookStore()) is None  # no candidates -> None


def test_embedding_selector_fail_safe_on_model_error(monkeypatch):
    def _boom(texts):
        raise RuntimeError("model load failed")
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_boom), raising=False)
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    hyp = RootCauseHypothesis(situation_id="sit-1", description="x", confidence=0.2)
    assert sel.select(_situation(), hyp, _store()) is None  # error -> None, never raises
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest <the test path> -k embedding -v`
Expected: FAIL — no `EmbeddingRunbookSelector`.

- [ ] **Step 3: Implement `EmbeddingRunbookSelector`**

Add to `services/rca/adapters/runbook_selector.py`. Structure encode behind a staticmethod `_encode(texts) -> np.ndarray` that the test stubs; the real `_encode` lazily loads the model. Wrap the whole `select` body in try/except → None.

```python
import logging

logger = logging.getLogger("intelliops.rca.runbook_selector")

_MODEL_CACHE: dict = {}


class EmbeddingRunbookSelector:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2", threshold: float = 0.45):
        self._model_name = model_name
        self._threshold = threshold
        self._symptom_cache: dict = {}  # keyed by f"{pb.id}:{hash(symptoms)}" -> vector

    def _encode(self, texts):
        # lazy import + cached model; overridden in tests
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415 — lazy, slim-boundary

        model = _MODEL_CACHE.get(self._model_name)
        if model is None:
            model = SentenceTransformer(self._model_name)
            _MODEL_CACHE[self._model_name] = model
        return model.encode(texts)

    def _query_text(self, situation, hypothesis) -> str:
        names = " ".join(e.name for e in situation.member_events)
        return f"{hypothesis.description}. signals: {names}".strip()

    def select(self, situation, hypothesis, store):
        try:
            import numpy as np  # noqa: PLC0415

            candidates = [p for p in store.list() if getattr(p, "symptoms", None)]
            if not candidates:
                return None
            query_vec = np.asarray(self._encode([self._query_text(situation, hypothesis)]))[0]
            symptom_texts = [p.symptoms for p in candidates]
            symptom_vecs = np.asarray(self._encode(symptom_texts))

            def _cos(a, b):
                na, nb = np.linalg.norm(a), np.linalg.norm(b)
                return float(a @ b / (na * nb)) if na and nb else 0.0

            scores = [_cos(query_vec, sv) for sv in symptom_vecs]
            best_i = int(np.argmax(scores))
            best_score = scores[best_i]
            if best_score >= self._threshold:
                return candidates[best_i].id, best_score
            return None
        except Exception as exc:  # noqa: BLE001 — fail-safe, never raise out of diagnose
            logger.info("embedding runbook selection failed (%s); no semantic match", exc.__class__.__name__)
            return None
```

(Note: the test stubs `_encode` as a staticmethod; the real one is an instance method using the cache — the monkeypatch with `staticmethod(...)` + `raising=False` replaces it for the test. If that binding is awkward, refactor `_encode` to a module-level function `_encode_texts(model_name, texts)` and have the test monkeypatch that instead — either way the injection point must let the test supply deterministic vectors. Pick the cleaner one.)

Add `sentence-transformers>=3.0` to the `ml` extra in `pyproject.toml`:
```python
ml = ["numpy>=2", "scikit-learn>=1.9.0", "river>=0.25.0", "joblib>=1.5.3", "sentence-transformers>=3.0"]
```

- [ ] **Step 4: Run the embedding tests**

Run: `uv run pytest <the test path> -v`
Expected: PASS (the fake-encode logic + closed-catalog + fail-safe tests).

- [ ] **Step 5: Slim-boundary check — action/governance/feedback still import WITHOUT sentence-transformers**

Run: `uv run python -c "import services.action.app; import services.governance.app; import services.feedback.app; print('slim OK')"` and confirm `sentence-transformers` is NOT pulled in: `uv run python -c "import sys; import services.action.app; print('sentence_transformers' in sys.modules)"` → must print `False`. Also confirm importing the selector module itself doesn't import the model at module load: `uv run python -c "import sys; import services.rca.adapters.runbook_selector; print('sentence_transformers' in sys.modules)"` → `False` (lazy import inside `_encode`).

- [ ] **Step 6: Full suite + lint**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. (The default suite does NOT load a real model — only the fake-encode tests run; the `EmbeddingRunbookSelector` is constructed only when `runbook_selector_mode=embedding`.)

- [ ] **Step 7: Commit**

```bash
git add services/rca/adapters/runbook_selector.py pyproject.toml <the test path>
git commit -m "feat(selection): EmbeddingRunbookSelector — cosine similarity over playbook symptoms, fail-safe

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Docs (+ optional UI provenance chip)

**Files:**
- Modify: `deploy/k8s/README.md` (or an RCA README section)
- Optional: `frontend/src/data/types.ts` + `frontend/src/views/Incidents.tsx` (a "semantic match" provenance chip)
- Commit the spec + this plan (untracked) onto the branch.

- [ ] **Step 1: Document how selection works**

Add a section covering the selection flow honestly:
- **Rules first:** deterministic keyword rules (recent-deploy → rollback, saturation tokens → scale, log/error → restart) — fast, high-precision, fully auditable.
- **Semantic fallback:** when no rule fires, `EmbeddingRunbookSelector` embeds the incident's symptoms + the hypothesis and ranks the registered playbooks by cosine similarity against each playbook's curated `symptoms`, picking the best match ≥ threshold. It can ONLY rank existing, human-vetted playbooks — it never invents a runbook or an action. This is *retrieval*, not an LLM choosing.
- **Gap:** below threshold → no match → the AI-authoring flow (PR C) can draft a candidate for human approval.
- **Enabling it:** `INTELLIOPS_RUNBOOK_SELECTOR_MODE=embedding` + the `ml` extra installed (pulls `sentence-transformers`, ~80MB model `all-MiniLM-L6-v2`, runs offline). Default `off` → rules-only (today's behavior).
- The honest framing: "semantic matching among vetted playbooks, deterministic given the vectors — not an LLM deciding the fix."

- [ ] **Step 2: (Optional) UI provenance chip**

If doing the UI: surface the semantic-match provenance (the `semantic match: {id} ({score})` evidence line already flows through the hypothesis evidence, so it may already render in the incident panel's evidence list — check `Incidents.tsx`). Optionally add a distinct "🔎 semantic" chip. Keep it honest (rule vs semantic vs LLM-explanation are different provenances). `npm --prefix frontend run build` must stay clean.

- [ ] **Step 3: Final gates + slim-boundary**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && uv run python -c "import sys; import services.action.app; print('sentence_transformers' in sys.modules)"` (→ False) and `npm --prefix frontend run build` (if UI touched).
Expected: all green + slim-boundary holds.

- [ ] **Step 4: Commit**

```bash
git add deploy/k8s/README.md docs/superpowers/specs/2026-09-05-semantic-runbook-selection-design.md docs/superpowers/plans/2026-09-05-semantic-runbook-selection.md
# + frontend files if the optional chip was done
git commit -m "docs(selection): how runbook selection works (rules -> semantic -> gap); spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 contract+symptoms → Task 1; §2 protocol → Task 1; §3 adapters → Task 2 (Null) + Task 3 (Embedding); §4 wiring → Task 2; §5 config+factory → Task 1 (config) + Task 2 (factory); §6 dep → Task 3; §7 frontend → Task 4 (optional). Acceptance criteria 1-8 mapped.
- **Safety invariant provable at each step:** Task 2 proves `select_runbook` only honors a registered id (`store.get(pid) is not None`) and that Null reproduces today's behavior; Task 3 proves the embedding selector returns None on empty store (closed catalog) and on any error (fail-safe); the slim-boundary check (Task 3 Step 5) proves action/governance/feedback don't gain a transformers import.
- **Augments-not-replaces:** Task 2's `test_rule_wins_and_selector_not_consulted` asserts the selector is NOT called when a rule fires — the keyword rules stay primary.
- **Type consistency:** `RunbookSelector.select -> tuple[str, float] | None` identical across the protocol (T1), Null (T2), Embedding (T3); `select_runbook -> tuple[Playbook | None, float | None, str]` consistent in rank.py (T2) and consumer.py usage (T2).
- **Test-safe default:** `runbook_selector_mode="off"` → Null; the default suite never loads a real model (only fake-encode tests run); `sentence-transformers` in the ml extra only. Each task's full-suite run asserts the base stays green.
- **Known soft spot (flag for the executor):** the `_encode` injection point for tests — the plan gives two options (staticmethod override vs a module-level `_encode_texts`); the executor picks whichever binds cleanly so the deterministic cosine test works without a real model. Keep the test contract (fake vectors → scale-service match; error → None).
