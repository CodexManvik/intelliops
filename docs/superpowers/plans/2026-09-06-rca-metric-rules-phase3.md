# Richer RCA rules + AI-computed confidence Implementation Plan (Metrics Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend RCA's rules to map the new metric families to the right runbook, and let the **embedding model compute each hypothesis's confidence** (the fit of a candidate runbook to the incident, reusing PR D's cosine machinery) — so the AI chooses which runbook helps, while deterministic ranking + HITL still make the binding call. Off by default = byte-identical.

**Architecture:** Two-layer diagnosis. Keyword rules in `rank_hypotheses` PROPOSE candidate hypotheses (closed set: restart-pod/scale-service/rollback-deploy), each with a fallback confidence constant. The `EmbeddingRunbookSelector` (extended with a per-candidate `score(...)`) then COMPUTES the confidence as the cosine fit of the incident's symptoms against the candidate runbook's `symptoms` text — replacing the constant when available, else the constant stands. The memory-leak mis-mapping (scale→restart) is resolved by fit, not a fragile constant. Reuses the existing `RUNBOOK_SELECTOR_MODE` knob (off by default → hardcoded confidences).

**Tech Stack:** Python 3.11/3.12, pytest; `sentence_transformers` (already behind the lazy import in `runbook_selector.py`); no new deps.

**Spec:** `docs/superpowers/specs/2026-09-06-rca-metric-rules-phase3-design.md` (read alongside). **Phase 3 of a 4-phase metrics arc** (P1 #38, P2 #39 merged; P4: per-metric health).

## Global Constraints

- **Branch `feat/rca-metric-rules-phase3` off current master** (Phases 1+2 merged; PR D's `EmbeddingRunbookSelector`/`RunbookSelector`/`select_runbook` are on master).
- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~529 base + new tests); `ruff check .` + `ruff format --check .` clean.
- **Env:** `uv sync --extra ml --extra k8s` once.
- **SAFETY INVARIANTS (load-bearing):**
  - **Off is byte-identical:** with `RUNBOOK_SELECTOR_MODE=off` (default → `NullRunbookSelector`, whose `score` returns `None`), each rule uses its EXISTING hardcoded confidence and today's deploy(0.8)>saturation(0.6)>error(0.5) ordering — the ~529 base suite + every Phase-1 diagnosis unchanged. The AI-confidence path is opt-in.
  - **error→restart invariant (off AND on):** an `error`/`dependency_outage` incident (cpu held flat by Phase 1) diagnoses to `restart-pod`, NEVER `scale-service`. This must hold with the selector off (fallback ordering) and on (embedding fit — error symptoms match restart best). Tests assert both.
  - **Closed catalog + fail-safe:** a rule only ever proposes one of the 3 vetted runbooks; the embedding only SCORES rule-proposed candidates (never fabricates an id); `store.get(id) is not None` still enforced; any embedding error → the hypothesis keeps its rule confidence (the selector `score`/`select` NEVER raises out of ranking).
  - **No new runbook / no new remediation action / no gate change.** Phase 3 is diagnosis routing + confidence only. The closed `RemediationStep` Literal, the denylist, and the sandbox are untouched.
- **Slim-boundary holds:** the `sentence_transformers` import stays LAZY inside `runbook_selector.py` (`_encode`/`score`); RCA is where it lives. No leak into slim services — CI slim-boundary must stay green.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** push; open a PR against master; the USER merges. Never merge to master.

---

## File Structure

- `services/rca/adapters/runbook_selector.py` — add `score(situation, hypothesis, playbook) -> float | None` to `EmbeddingRunbookSelector` + `NullRunbookSelector`; re-express `select` via `score`.
- `services/rca/rank.py` — refined + new metric-family rules; `rank_hypotheses` gains a `selector` param; embedding-computed confidence + `confidence_source` provenance.
- `services/rca/consumer.py` — build `sel` before `rank_hypotheses` and pass it in.
- `playbooks/{restart-pod,scale-service,rollback-deploy}.yaml` + `deploy/playbooks/{...}.yaml` — sharpen `symptoms` so the fit routes each family.
- `common/contracts.py` — maybe add `confidence_source` to `RootCauseHypothesis` (optional, additive) — OR carry provenance in `evidence` (decide in Task 3; prefer additive field for cleanliness).
- Docs: `architectural.md` (ADR-028), `flow.md` (§5.3), `docs/MERIDIAN.md`, `README.md` (count 27→28).
- Tests: `services/rca/tests/` (existing dir — extend `test_rank.py`, add selector-score tests).

**PINNED fallback confidences (used when the embedding is off/unavailable — preserve today's ordering):**
`rollback-deploy` 0.80 · `memory→restart-pod` 0.65 · `saturation→scale-service` 0.60 · `latency/queue_depth/request_rate→scale-service` 0.55 · `db_pool→restart-pod` 0.55 · `log-or-error→restart-pod` 0.50 · fallback (no runbook) 0.20.

**PINNED metric-family → candidate runbook rules:**
- deploy marker → `rollback-deploy`
- `memory_usage_mb` present (leak/high) → `restart-pod` (ranked above saturation; remove "mem"/"memory" from `_SATURATION_TOKENS` so memory doesn't ALSO fire scale)
- `cpu`/`disk`/`saturation` tokens → `scale-service`
- `latency`/`queue_depth`/`request_rate` tokens → `scale-service`
- `db_pool` tokens (`db_pool_in_use`) → `restart-pod`
- log kind or `error` in name → `restart-pod`

---

## Task 1: Expose per-candidate `score` on the selector

**Files:**
- Modify: `services/rca/adapters/runbook_selector.py`
- Test: `services/rca/tests/` (extend the embedding-selector test file — grep for it, likely `test_runbook_selector.py` or in `test_embedding_selector`)

**Interfaces:**
- Produces: `EmbeddingRunbookSelector.score(situation, hypothesis, playbook) -> float | None` (cosine fit of the incident+hypothesis query against `playbook.symptoms`; `None` if no symptoms / model error; NEVER raises). `NullRunbookSelector.score(...) -> None`. `select` re-expressed to score every candidate and take the argmax ≥ threshold (one scoring path).

- [ ] **Step 1: Write the failing test**

Extend the existing embedding-selector test (it already injects a fake `_encode` via monkeypatch/staticmethod — reuse that pattern; grep `services/rca/tests/` for `_encode`/`EmbeddingRunbookSelector`). Add:

```python
def test_score_returns_cosine_fit_for_a_playbook(monkeypatch):
    # fake-encode: map known texts to fixed vectors so cosine is deterministic
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False)
    sel = rs.EmbeddingRunbookSelector()
    pb = _playbook(id="restart-pod", symptoms="crash loops, memory leak, recycle process")
    s = sel.score(_situation("memory_usage_mb"), _hyp("memory leak trending to OOM"), pb)
    assert s is not None and 0.0 <= s <= 1.0

def test_score_none_when_playbook_has_no_symptoms(monkeypatch):
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False)
    sel = rs.EmbeddingRunbookSelector()
    pb = _playbook(id="x", symptoms=None)
    assert sel.score(_situation(), _hyp("x"), pb) is None

def test_score_none_on_encode_error(monkeypatch):
    def _boom(texts): raise RuntimeError("model down")
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_boom), raising=False)
    sel = rs.EmbeddingRunbookSelector()
    pb = _playbook(id="x", symptoms="something")
    assert sel.score(_situation(), _hyp("x"), pb) is None  # never raises

def test_null_selector_score_is_none():
    from services.rca.adapters.runbook_selector import NullRunbookSelector
    assert NullRunbookSelector().score(_situation(), _hyp("x"), _playbook(id="x", symptoms="s")) is None

def test_select_still_works_via_score(monkeypatch):
    # select must still return the best (id, score) >= threshold — re-expressed via score
    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False)
    ...  # match the existing select test's assertion
```

(Reuse the existing test file's `_fake_encode`, `_situation`, `_playbook` helpers; add a `_hyp(desc)` building a `RootCauseHypothesis(situation_id=..., description=desc, confidence=0.2)`. If the existing test's fake-encode maps by keyword, keep score deterministic.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/rca/tests/ -k score -v`
Expected: FAIL — `score` doesn't exist.

- [ ] **Step 3: Implement `score` + re-express `select`**

In `runbook_selector.py`, factor the cosine computation into `score(situation, hypothesis, playbook)`:
```python
def score(self, situation, hypothesis, playbook) -> float | None:
    symptoms = getattr(playbook, "symptoms", None)
    if not symptoms:
        return None
    try:
        import numpy as np
        q = np.asarray(self._encode([self._query_text(situation, hypothesis)]))[0]
        s = np.asarray(self._encode([symptoms]))[0]
        denom = (np.linalg.norm(q) * np.linalg.norm(s))
        if denom == 0:
            return None
        return float(np.dot(q, s) / denom)
    except Exception:  # noqa: BLE001 — fail-safe, never raise
        return None
```
Re-express `select` to iterate `store.list()` candidates, call `score` for each, and return the argmax `(id, score)` if `>= threshold` else `None` (keeps ONE scoring path; the existing select tests must still pass). `NullRunbookSelector.score -> None`.

- [ ] **Step 4: Run tests + full suite + lint + slim check**

Run: `uv run pytest services/rca/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && uv run python -c "import sys; import services.action.app; print('numpy' in sys.modules)"`
Expected: green + the slim check prints `False` (score's numpy/sentence_transformers imports stay lazy).

- [ ] **Step 5: Commit**

```bash
git add services/rca/adapters/runbook_selector.py services/rca/tests/
git commit -m "feat(rca): expose per-candidate score() on the runbook selector (fail-safe, lazy)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Refined + new metric-family rules (candidate layer, off-path)

**Files:**
- Modify: `services/rca/rank.py` (the rules + `_SATURATION_TOKENS`; add optional `selector` param, NOT yet used for confidence)
- Test: `services/rca/tests/test_rank.py` (extend)

**Interfaces:**
- Consumes: existing `rank_hypotheses` structure.
- Produces: new/refined rules proposing candidates + the PINNED fallback confidences; `rank_hypotheses(situation, context, reliability_provider=None, selector=None)` — the `selector` param is ADDED here (default null) but Task 2 does NOT use it for confidence yet (Task 3 does). Task 2 proves the OFF path (fallback confidences) gives the intended diagnoses.

**RULING:** Task 2 is the deterministic candidate layer. Add `selector=None` to the signature so Task 3 can use it without another signature change, but Task 2's `rank_hypotheses` behavior with the fallback confidences must give: memory→restart (0.65 > saturation 0.60), db_pool→restart, latency/queue/request_rate→scale, error→restart, saturation→scale, deploy→rollback. Remove "mem"/"memory" from `_SATURATION_TOKENS` (so memory fires ONLY the restart candidate, not also scale).

- [ ] **Step 1: Write the failing tests**

Extend `test_rank.py` (grep it for the existing rule-test style + how it builds a `Situation` with member events of a given metric name + a `context`). Add tests asserting the top hypothesis's `suggested_runbook_id` for each family (selector=None, i.e. off):

```python
def test_memory_leak_maps_to_restart_not_scale():
    sit = _situation_with_metric("memory_usage_mb", value=800.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"  # NOT scale-service

def test_db_pool_exhaustion_maps_to_restart():
    sit = _situation_with_metric("db_pool_in_use", value=20.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "restart-pod"

def test_latency_maps_to_scale():
    sit = _situation_with_metric("latency_p99_ms", value=700.0)
    hyps = rank_hypotheses(sit, EnrichmentContext())
    assert hyps[0].suggested_runbook_id == "scale-service"

def test_queue_depth_maps_to_scale():
    sit = _situation_with_metric("queue_depth", value=50.0)
    assert rank_hypotheses(sit, EnrichmentContext())[0].suggested_runbook_id == "scale-service"

def test_error_still_maps_to_restart_not_scale():  # the load-bearing invariant, off
    sit = _situation_with_metric("meridian_error_rate", value=0.5)
    assert rank_hypotheses(sit, EnrichmentContext())[0].suggested_runbook_id == "restart-pod"

def test_cpu_saturation_still_maps_to_scale():
    sit = _situation_with_metric("cpu_usage", value=95.0)
    assert rank_hypotheses(sit, EnrichmentContext())[0].suggested_runbook_id == "scale-service"

def test_existing_deploy_rule_unchanged():
    # a recent-deploy context still → rollback-deploy at 0.8 (top)
    ...  # reuse the existing deploy-rule test setup
```

(Build `_situation_with_metric(name, value)` — a `Situation` with one `TelemetryEvent` of that name/value and a `service` label; reuse test_rank.py's existing helpers.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/rca/tests/test_rank.py -v`
Expected: FAIL — memory→scale today (not restart); db_pool/latency/queue have no rule.

- [ ] **Step 3: Refine + add the rules**

In `rank.py`: remove `"mem", "memory"` from `_SATURATION_TOKENS`. Add rules (each appends a `RootCauseHypothesis` with the candidate runbook + PINNED fallback confidence):
- memory (`memory_usage_mb` / "memory" token present) → `restart-pod`, confidence 0.65.
- db_pool (`db_pool` token) → `restart-pod`, confidence 0.55.
- latency/queue/request_rate (`latency`/`queue_depth`/`request_rate` tokens) → `scale-service`, confidence 0.55.
- (saturation/error/deploy/fallback unchanged except memory removed from saturation tokens.)
Add `selector=None` to the `rank_hypotheses` signature (unused this task). Keep the confidence-sort + reliability-boost logic as-is. Ensure the ordering constants give the intended top hypothesis per family (test-driven).

- [ ] **Step 4: Run tests + full suite + lint**

Run: `uv run pytest services/rca/tests/test_rank.py -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. Existing RCA/diagnose tests must still pass (the deploy/saturation/error mappings are preserved; memory moved from scale to restart — update any existing test that asserted memory→scale, if one exists, to the corrected memory→restart; note it as an intentional diagnosis correction).

- [ ] **Step 5: Commit**

```bash
git add services/rca/rank.py services/rca/tests/test_rank.py
git commit -m "feat(rca): metric-family rules — memory/db_pool -> restart, latency/queue/surge -> scale

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Embedding-computed confidence + provenance

**Files:**
- Modify: `services/rca/rank.py` (use `selector.score` for confidence when available)
- Modify: `common/contracts.py` (add `confidence_source: str | None = None` to `RootCauseHypothesis` — additive)
- Test: `services/rca/tests/test_rank.py` (extend — stub selector)

**Interfaces:**
- Consumes: `RunbookSelector.score` (Task 1).
- Produces: in `rank_hypotheses`, for each hypothesis with a `suggested_runbook_id`, if `selector.score(situation, hypothesis, store.get(runbook_id))` returns a float, that becomes the hypothesis's `confidence` (clamped 0..1) and `confidence_source="embedding"`; else the fallback constant stands with `confidence_source="rule"`. `RootCauseHypothesis.confidence_source: str | None = None`.

**RULING:** the embedding confidence REPLACES the fallback when available; the reliability boost still applies on top of the resulting confidence. The final ranking is by the (possibly embedding-computed) confidence. The `store` is needed to resolve the candidate playbook for scoring — `rank_hypotheses` gains a `store` param OR the scoring is done against a playbook the selector fetches; simplest: pass `store` into `rank_hypotheses` too (thread from `diagnose`). If adding `store` to `rank_hypotheses` is awkward, do the embedding re-scoring in `diagnose` AFTER `rank_hypotheses` (which has `store`) — decide for minimal disruption; either way a hypothesis's confidence ends up embedding-computed when the selector is on. PIN the choice here: **pass `store` and `selector` into `rank_hypotheses`** (it already takes `situation, context`; add `store=None, selector=None` — when either is None, skip embedding scoring → fallback constants, the off path).

- [ ] **Step 1: Write the failing tests**

```python
class _StubSelector:
    def __init__(self, scores):  # {(runbook_id): score}
        self._scores = scores
    def score(self, situation, hypothesis, playbook):
        return self._scores.get(playbook.id)
    def select(self, *a, **k):
        return None

def test_embedding_score_becomes_confidence(_store_with_playbooks):
    sit = _situation_with_metric("cpu_usage", value=95.0)  # rule proposes scale-service @0.60
    sel = _StubSelector({"scale-service": 0.91})
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_store_with_playbooks, selector=sel)
    top = hyps[0]
    assert top.suggested_runbook_id == "scale-service"
    assert abs(top.confidence - 0.91) < 1e-6          # embedding score, not the 0.60 constant
    assert top.confidence_source == "embedding"

def test_falls_back_to_rule_confidence_when_selector_none():
    sit = _situation_with_metric("cpu_usage", value=95.0)
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=None, selector=None)
    assert hyps[0].confidence == 0.60 and hyps[0].confidence_source in (None, "rule")

def test_embedding_error_keeps_rule_confidence(_store_with_playbooks):
    class _Raises:
        def score(self, *a, **k): raise RuntimeError("boom")  # score should be caught by selector; but rank must be robust
        def select(self, *a, **k): return None
    # If score can raise, rank_hypotheses must guard it -> rule confidence stands
    sit = _situation_with_metric("cpu_usage", value=95.0)
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_store_with_playbooks, selector=_Raises())
    assert hyps[0].confidence == 0.60  # unchanged; ranking never raised

def test_memory_leak_restart_wins_on_embedding_fit(_store_with_playbooks):
    # embedding scores restart-pod higher than scale for a memory-leak incident
    sit = _situation_with_metric("memory_usage_mb", value=900.0)
    sel = _StubSelector({"restart-pod": 0.88, "scale-service": 0.40})
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_store_with_playbooks, selector=sel)
    assert hyps[0].suggested_runbook_id == "restart-pod"

def test_error_restart_invariant_holds_on(_store_with_playbooks):
    sit = _situation_with_metric("meridian_error_rate", value=0.5)
    sel = _StubSelector({"restart-pod": 0.80, "scale-service": 0.30})
    hyps = rank_hypotheses(sit, EnrichmentContext(), store=_store_with_playbooks, selector=sel)
    assert hyps[0].suggested_runbook_id == "restart-pod"  # never scale, on
```

(`_store_with_playbooks` is an `InMemoryPlaybookStore` seeded with restart-pod/scale-service/rollback-deploy playbooks that have `symptoms` — reuse a helper.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest services/rca/tests/test_rank.py -k "embedding or confidence or invariant" -v`
Expected: FAIL — confidence isn't embedding-computed; `confidence_source` field absent.

- [ ] **Step 3: Add `confidence_source` + the embedding-confidence logic**

In `common/contracts.py`, add to `RootCauseHypothesis`: `confidence_source: str | None = None` (additive, after `explanation_source` or similar).
In `rank.py`, add `store=None, selector=None` to `rank_hypotheses`. After building the candidate hypotheses (Task 2), before the final sort: for each hypothesis with a `suggested_runbook_id`, if `store is not None and selector is not None`, resolve `pb = store.get(id)` and (guarding any exception) `s = selector.score(situation, hyp, pb)`; if `s is not None`, replace the hypothesis's confidence with `min(1.0, max(0.0, s))` and set `confidence_source="embedding"`; else set `confidence_source="rule"`. Then the existing confidence-sort + reliability boost run on the resulting confidences. Guard `selector.score` in a try/except → keep rule confidence (belt; the selector should already be fail-safe from Task 1).

- [ ] **Step 4: Run tests + full suite + lint**

Run: `uv run pytest services/rca/tests/test_rank.py -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: green. The off path (`store`/`selector` None) keeps the fallback constants + today's behavior — existing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add services/rca/rank.py common/contracts.py services/rca/tests/test_rank.py
git commit -m "feat(rca): embedding-computed confidence per hypothesis (off-default -> rule constants); confidence_source

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Sharpen playbook symptoms + wire selector into ranking

**Files:**
- Modify: `playbooks/{restart-pod,scale-service,rollback-deploy}.yaml` + `deploy/playbooks/{...}.yaml` (both dirs — the live seed is `deploy/playbooks/`, per Phase 1)
- Modify: `services/rca/consumer.py` (`diagnose` — build `sel` before `rank_hypotheses`, pass `store` + `sel` in)
- Test: `services/rca/tests/` (a diagnose-level test that the wiring produces embedding confidence when the selector is on)

**Interfaces:**
- Consumes: Task 3's `rank_hypotheses(situation, context, reliability_provider, store, selector)`.
- Produces: `diagnose` passes `store` + the built `sel` into `rank_hypotheses`; the sharpened `symptoms` route each family to the intended runbook via fit.

- [ ] **Step 1: Sharpen the symptoms (both playbook dirs)**

Update `symptoms` in ALL of `playbooks/` AND `deploy/playbooks/` (keep them identical), so the cosine fit routes correctly:
- `restart-pod`: "crash loops, wedged or hung process, memory leak trending to OOM, database connection-pool exhaustion, elevated error rate, stuck workers — a process that must be recycled"
- `scale-service`: "CPU or resource saturation, high latency under load, growing queue depth, a traffic surge or capacity shortfall — needs more replicas"
- `rollback-deploy`: "a regression immediately after a recent deployment or release, a bad new version misbehaving — revert to the prior revision"

- [ ] **Step 2: Write the failing test (diagnose wiring)**

Add a test that `diagnose(...)` with an enabled stub/fake selector yields a top hypothesis whose `confidence_source == "embedding"` (proving `diagnose` threads `store`+`selector` into `rank_hypotheses`). Reuse the consumer test helpers (`test_consumer.py` builds `diagnose` calls). With a null selector (default), `confidence_source` is `None`/`"rule"` and the diagnosis is unchanged.

- [ ] **Step 3: Wire `diagnose`**

In `consumer.py` `diagnose`: move `sel = selector or NullRunbookSelector()` ABOVE the `rank_hypotheses` call, and change that call to `rank_hypotheses(situation, context, reliability_provider, store=store, selector=sel)`. (Keep the subsequent `select_runbook(hypotheses, situation, store, sel)` for the no-rule semantic fallback — unchanged.) Optionally thread `confidence_source` into the projection/UI (best-effort — see the projection's hypothesis dict; additive like `explanation_source`).

- [ ] **Step 4: Run tests + full suite + lint + slim check**

Run: `uv run pytest services/rca/tests/ -v && uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check . && uv run python -c "import sys; import services.action.app; print('numpy' in sys.modules)"`
Expected: green + slim check `False`.

- [ ] **Step 5: Commit**

```bash
git add playbooks/ deploy/playbooks/ services/rca/consumer.py services/rca/tests/
git commit -m "feat(rca): sharpen playbook symptoms for fit routing; wire selector into rank_hypotheses

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Docs + final gates

**Files:**
- Modify: `architectural.md` (ADR-028), `flow.md` (§5.3), `docs/MERIDIAN.md`, `README.md` (count 27→28)
- Commit the spec + this plan onto the branch.

- [ ] **Step 1: architectural.md ADR-028**

Add `### ADR-028 — RCA metric-family rules + AI-computed confidence` before `## 4. Cross-cutting concerns` (after ADR-027), in the house Context→Decision→Why style. Cover: the 3-rule/hardcoded-confidence gap; the two-layer design (keyword rules PROPOSE candidates, the embedding COMPUTES the confidence as symptom-fit); memory-leak→restart resolved by fit not a constant; the new families→best-fit among the closed 3 runbooks (no new runbooks); off-by-default byte-identical + the error→restart invariant held off AND on; the honest framing — "the AI chooses the runbook by fit, but only among vetted rule-proposed candidates; deterministic ranking + HITL + the denylist + the sandbox still decide and gate." Cross-reference ADR-026 (the embedding selector) and ADR-024/023 (the denylist/sandbox that still gate). Update README "twenty-seven" → "twenty-eight" (both locations).

- [ ] **Step 2: flow.md §5.3 + MERIDIAN.md**

flow.md §5.3 (rca function reference): update the `select_runbook`/ranking note to describe the two-layer diagnosis (rules propose; embedding scores confidence when `RUNBOOK_SELECTOR_MODE=embedding`, else rule constants). MERIDIAN.md: note how the new fault families route (memory-leak→restart, db-exhaustion→restart, latency/queue/surge→scale) and that the confidence is embedding-computed when on.

- [ ] **Step 3: Final gates**

Run: `uv run pytest -m "not postgres and not kafka" -q && ruff check . && ruff format --check .`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add architectural.md flow.md docs/MERIDIAN.md README.md docs/superpowers/specs/2026-09-06-rca-metric-rules-phase3-design.md docs/superpowers/plans/2026-09-06-rca-metric-rules-phase3.md
git commit -m "docs(rca): ADR-028 two-layer diagnosis + AI-computed confidence; spec + plan

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 score → Task 1; §2 rules → Task 2; §2/§4 confidence → Task 3; §3 symptoms + §4 wiring → Task 4; docs → Task 5. Acceptance criteria 1–8 mapped.
- **Off-is-byte-identical proven at each step:** Task 2's off-path rule tests; Task 3's `test_falls_back_to_rule_confidence_when_selector_none`; the store/selector-None guard.
- **error→restart invariant off AND on:** Task 2's `test_error_still_maps_to_restart_not_scale` (off) + Task 3's `test_error_restart_invariant_holds_on`.
- **Fail-safe:** Task 1's `score` returns None on error; Task 3 additionally guards `selector.score` in try/except → rule confidence; neither raises out of ranking.
- **Closed catalog:** rules propose only the 3 vetted runbooks; the embedding only scores `store.get(id)` playbooks; no fabrication.
- **Type consistency:** `score(situation, hypothesis, playbook) -> float | None` across selector impls; `rank_hypotheses(situation, context, reliability_provider=None, store=None, selector=None)`; `confidence_source: str | None` on the hypothesis.
- **Known soft spot (flag for the executor):** Task 3's choice of threading `store`+`selector` into `rank_hypotheses` vs. re-scoring in `diagnose` — the plan PINS "pass into rank_hypotheses". If the reliability-boost interaction with an embedding-replaced confidence looks off (a very low embedding score sinking a should-fire hypothesis), keep the test contract (embedding score becomes the confidence) and note it; the reliability boost is bounded (0.15) and applies after, consistent with today.
