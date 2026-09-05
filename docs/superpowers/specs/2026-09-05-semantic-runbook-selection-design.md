# Semantic runbook selection (embedding similarity) — Design Spec (PR D)

**Date:** 2026-09-05
**Owner:** Manvik
**Status:** design (architectural — new `symptoms` field on `Playbook`, a `RunbookSelector` interface + adapters, an embedding dependency, and a change to the RCA ranking→selection flow). Standalone follow-on to the 3-PR arc (A: sandbox #33 · B: tier-2 vocab + denylist #34 · C: AI-authored runbooks #35 — all merged).

**Depends on:** nothing beyond current master. Branch off master.

## The problem

Today runbook selection is **pure keyword matching** in `services/rca/rank.py`: `if any(tok in metric_names for tok in _SATURATION_TOKENS)` → `scale-service`, `if "error" in name` → `restart-pod`, a recent-deploy hit → `rollback-deploy`, else a fallback with **no runbook** (the "gap"). This is brittle: a metric named `container_memory_working_set_bytes`, or a hypothesis worded "the service is thrashing under load," shares no literal token with `_SATURATION_TOKENS` and so **misses a perfectly good runbook** — the incident falls into the gap even though `scale-service` was the right answer. Real AIOps needs *semantic* matching, not substring matching.

But an LLM must NOT be the thing that picks the action — that reintroduces exactly the hallucination/unsafe risk the arc carefully designed out. The safe form of "intelligence in selection" is **retrieval**: embed the incident's symptoms, embed each human-vetted playbook's symptom profile, and pick the nearest match among the *existing* playbooks. The catalog stays closed; the intelligence is in the *matching*, never in *inventing* an action.

## Goal

Add **semantic runbook selection** that augments (not replaces) the keyword rules:
1. The existing keyword rules run first (fast, high-precision when they fire).
2. When no rule produces a confident suggestion, a `RunbookSelector` embeds the situation's signals + the top hypothesis's description, compares (cosine similarity) against each registered playbook's embedded **symptom profile**, and picks the best match **above a similarity threshold**.
3. Below threshold → genuinely no match → the gap → the PR C AI-authoring flow (draft a new runbook for human approval).

Every existing property is preserved: **auditable** (the chosen runbook + its similarity score are recorded), **deterministic given the vectors** (same inputs → same choice), **closed catalog** (the selector can only ever *rank existing playbooks*, never fabricate one or an action), and the **reliability track-record boost** still applies. Default OFF (a `NullRunbookSelector` = today's behavior exactly), opt-in for the demo.

## Key decisions (locked with the user)

1. **Embedding similarity, not an LLM classifier.** The selector RANKS among the existing, human-vetted playbooks by cosine similarity of embeddings. It cannot choose anything outside the registered set and cannot generate an action. (An LLM ranker was considered and rejected: slower, non-deterministic, and adds a model call to the diagnose hot path — the embedding approach is cheaper, offline-capable, and deterministic.)
2. **Augment, don't replace.** The keyword rules stay as the high-precision fast-path; the semantic selector is the fallback when the rules don't confidently fire. This keeps the obvious cases instant + fully explainable and adds semantic reach only where it's needed. The final binding choice is still made by deterministic code (threshold comparison), not by a model.
3. **Curated `symptoms` on the Playbook contract.** Add an optional `symptoms: str | None = None` field — a human-written description of when the runbook applies (e.g. "high CPU/memory saturation, service thrashing, OOM kills, sustained load"). The match is against **human intent**, not a guess derived from a terse id. Existing `match_rule` stays untouched. The three seed playbooks get symptom text.
4. **Local sentence-transformers, config-switched off.** A small local model (`all-MiniLM-L6-v2`, ~80MB, via `sentence-transformers`) computes embeddings offline — no API, no cost, fast. Cosine similarity via `numpy` (already an `ml` dep). Config-switched like every other adapter: `runbook_selector_mode` defaults `"off"` → `NullRunbookSelector` (base suite + CI byte-identical). The real `EmbeddingRunbookSelector` is opt-in.

## Non-goals / constraints

- **No LLM in selection** (decision 1). No model *chooses* the action; the selector ranks vetted playbooks by vector similarity. (The LLM's role stays: advisory explanations + AI-authored runbook *drafts* for human approval, from PRs already shipped.)
- **The keyword rules are not removed** (decision 2). PR D adds a fallback selector; `rank_hypotheses` keeps its rules. A rule that fires confidently wins without the selector running.
- **Test-safe default.** `runbook_selector_mode` defaults `"off"` → `NullRunbookSelector`, which yields exactly today's `surface_runbook` behavior. The base ~482-suite + CI are byte-identical. `sentence-transformers` goes in the `ml` optional-dependency group (the slim-boundary from PR #29 — action/governance/feedback must NOT gain a transformers import; RCA already carries ML weight). The real selector is opt-in via `RUNBOOK_SELECTOR_MODE=embedding` + the `ml` extra installed.
- **Fail-safe.** The selector never raises out of `diagnose`: any embedding/model error → fall back to the keyword-rule result (or None), mirroring the never-raise discipline of the LLM adapters. A missing/empty `symptoms` on a playbook just means it isn't a semantic candidate (skipped), not an error.
- **Explainable / no fabricated data.** When the selector picks a runbook, the choice records the matched playbook id + the similarity score (in the hypothesis evidence or the audit), so the UI/operator sees *why* ("matched 'resource exhaustion' runbook, similarity 0.91"). Never present a semantic guess as a rule-based certainty.
- **Additive contract.** `symptoms` is optional, defaults None; existing playbooks/tests unchanged.

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~482 base + new tests); `ruff check .` + `ruff format --check .` clean; `npm --prefix frontend run build` clean (if UI touched).
- **Slim-boundary:** `sentence-transformers` in the `ml` extra ONLY; the CI slim-boundary check (action/governance/feedback importable without ML) must stay green. RCA is where the selector lives.
- **Env:** `uv sync --extra ml --extra k8s` at setup.
- **Safety invariant:** the selector can only ever return an id of a **registered** playbook (it ranks `store`'s playbooks) or None; it never fabricates an id or an action. Default `"off"` reproduces today's behavior exactly.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/semantic-runbook-selection` off master. PR; user merges. Never merge to master.
- **Shared files:** `common/contracts.py`, `common/interfaces.py`, `common/config.py`, `services/rca/rank.py`, `services/rca/consumer.py`, `services/rca/app.py`, `services/rca/adapters/` (new selector), `playbooks/*.yaml`, `pyproject.toml`, and possibly the frontend (to show the match score).

---

## Design

### 1. Contract: `symptoms` on `Playbook` (`common/contracts.py`)

```python
class Playbook(BaseModel):
    id: str
    name: str
    match_rule: str
    steps: list[RemediationStep] = Field(default_factory=list)
    hitl_mode: HitlMode
    reversible: bool = False
    rollback_steps: list[RemediationStep] = Field(default_factory=list)
    symptoms: str | None = None  # human-written "when this applies" — the semantic match target
```

Additive/optional. The three seed playbooks (`playbooks/*.yaml`) gain `symptoms:` text:
- `scale-service`: "high CPU or memory saturation, resource exhaustion, service thrashing under sustained load, OOM kills, throttling"
- `restart-pod`: "error spikes in logs, crash loops, wedged or hung process, elevated 5xx, stuck workers"
- `rollback-deploy`: "regression immediately following a recent deployment or release, new version misbehaving, errors starting right after a rollout"

### 2. Interface: `RunbookSelector` (`common/interfaces.py`)

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

### 3. Adapters (`services/rca/adapters/runbook_selector.py`, new)

- **`NullRunbookSelector`** (default): `select(...) -> None`. No embeddings, no model — the fallback path just yields whatever the rules produced (or None → gap). Keeps base demo/tests byte-identical.
- **`EmbeddingRunbookSelector`** (`mode="embedding"`):
  1. Lazily load the sentence-transformers model (`all-MiniLM-L6-v2` by default; configurable) — imported INSIDE the adapter so the off path never imports it.
  2. Build the **query text** from the situation's signals + the hypothesis: metric/event names, any log snippets, and `hypothesis.description`.
  3. For each registered playbook with a non-empty `symptoms`, embed its `symptoms` (cache these — they change rarely; embed once per playbook set, keyed by playbook id+symptoms hash). Embed the query.
  4. Cosine similarity (numpy) query·symptom for each candidate; take the argmax.
  5. If the best score ≥ `runbook_selector_threshold` (default e.g. 0.45), return `(best_playbook_id, best_score)`; else return None (→ gap).
  6. **Any exception** → return None (fail-safe; the rules/None still stand). Never raises.

  (Implementation note: cache the playbook-symptom embeddings on the adapter instance, recomputed when the playbook set changes — a dict keyed by `f"{pb.id}:{hash(pb.symptoms)}"`. The query embedding is per-call. This keeps a diagnose call to one model.encode of the short query text.)

### 4. Wiring: augment `surface_runbook` in the RCA flow (`services/rca/rank.py` + `consumer.py`)

The selector runs as a **fallback after the rules**. Cleanest shape: `diagnose()` (`consumer.py`) gains a `selector` param and calls a new helper that tries the rules' `surface_runbook` first, then the selector:

```python
# services/rca/rank.py (new helper, or fold into surface_runbook's caller)
def select_runbook(
    hypotheses: list[RootCauseHypothesis],
    situation: Situation,
    store: PlaybookStore,
    selector,  # RunbookSelector
) -> tuple[Playbook | None, float | None, str]:
    """Returns (playbook, score, source). source ∈ {"rule","semantic","none"}.
    Rules win when they produce a runbook; otherwise the semantic selector tries."""
    rule_runbook = surface_runbook(hypotheses, store)  # existing keyword path
    if rule_runbook is not None:
        return rule_runbook, None, "rule"
    if hypotheses:
        hit = selector.select(situation, hypotheses[0], store)
        if hit is not None:
            pid, score = hit
            pb = store.get(pid)
            if pb is not None:
                return pb, score, "semantic"
    return None, None, "none"
```

`diagnose()` uses this; when `source == "semantic"`, it records the score + source on the top hypothesis's evidence (e.g. append `f"semantic match: {pid} ({score:.2f})"`) and sets `suggested_runbook_id` accordingly. The existing rule/reliability path is unchanged when a rule fires. `NullRunbookSelector.select` returns None, so with the default the whole thing collapses to exactly today's `surface_runbook` behavior.

### 5. Factory + config (`services/rca/app.py` + `common/config.py`)

Config:
```python
    runbook_selector_mode: str = "off"                     # "off" | "embedding"
    runbook_selector_model: str = "all-MiniLM-L6-v2"
    runbook_selector_threshold: float = 0.45               # min cosine similarity to accept a match
```
Factory `_make_runbook_selector(settings)` in `rca/app.py`: `embedding` → `EmbeddingRunbookSelector(model=..., threshold=...)`, else `NullRunbookSelector()`. Threaded into `run_consumer`/`diagnose` like `explainer`/`reliability_provider`.

### 6. Dependency (`pyproject.toml`)

Add to the `ml` optional-dependency group ONLY (slim-boundary):
```python
ml = ["numpy>=2", "scikit-learn>=1.9.0", "river>=0.25.0", "joblib>=1.5.3", "sentence-transformers>=3.0"]
```
The action/governance/feedback slim-import check must stay green — `sentence-transformers` is imported only inside `EmbeddingRunbookSelector` (lazy), which lives in RCA.

### 7. Frontend (optional, best-effort)

When a suggestion came from the semantic selector, the incident panel can show a small "🔎 semantic match ({score})" chip beside the suggested runbook, distinct from a rule-based suggestion — honest provenance, mirroring how `explanation_source: "llm"|"template"` is already surfaced. Additive; not required for correctness.

---

## Acceptance criteria

1. **Semantic match catches what keywords miss:** unit test — a situation whose signals/hypothesis semantically mean "resource saturation" but share NO literal token with `_SATURATION_TOKENS` (e.g. metric `container_memory_working_set_bytes`, hypothesis "service thrashing under load") is matched to `scale-service` by `EmbeddingRunbookSelector` (score ≥ threshold), where the keyword rules would have produced the gap. (This test needs the model; mark it appropriately or use a tiny deterministic fake-embedding selector for the logic test + a real-model test gated on the `ml` extra.)
2. **Closed catalog / never fabricates:** the selector only ever returns an id present in `store`; unit test — with an empty store or all-empty-`symptoms` playbooks, `select` returns None (→ gap), never a made-up id.
3. **Augments, doesn't replace:** unit test over the `select_runbook` helper — (a) when a keyword rule fires (rule produces a runbook), the result source is `"rule"` and the selector is NOT consulted; (b) when no rule fires, the selector runs and a match yields source `"semantic"` with the score; (c) no rule + no semantic match → source `"none"` (gap).
4. **Fail-safe:** `EmbeddingRunbookSelector.select` returns None on any internal error (e.g. model load failure) and never raises out of `diagnose`. Unit test with a selector whose model load is monkeypatched to raise → None.
5. **Test-safe default:** `runbook_selector_mode` defaults `"off"` → `NullRunbookSelector`; `diagnose` with the default reproduces today's `surface_runbook` behavior exactly; the base 482-suite is byte-identical (no existing RCA test changes). Slim-boundary check green (`sentence-transformers` only reachable through the ml extra + the lazy import).
6. **Explainable:** a semantic selection records the matched playbook id + score (on the hypothesis evidence and/or audit), so the provenance is visible. No semantic guess is presented as a rule certainty.
7. **Gates green:** ~482 + new tests; ruff clean; frontend build clean (if touched); slim-boundary CI green.
8. **(Manual, documented)** with the `ml` extra + `RUNBOOK_SELECTOR_MODE=embedding`, an incident that the old keyword rules would have missed now gets the right runbook suggested with a visible similarity score. Documented (a short RCA/README section on how selection works: rules → semantic fallback → gap).

## Suggested task ordering (for the plan)

1. **Contract + seed symptoms:** add `symptoms` to `Playbook`; add symptom text to the 3 seed playbooks; the `RunbookSelector` protocol; the config fields. Unit tests: contract additive; seed playbooks load with symptoms. (Green — nothing uses them yet.)
2. **The selection helper + Null path:** `select_runbook` in `rank.py` (rules-first, then selector) + `NullRunbookSelector`; wire `selector` through `diagnose`/`run_consumer`/`app.py` factory (default Null). Unit tests: the 3 augment cases (rule-wins / semantic / none) using a small deterministic FAKE selector (returns a fixed (id, score) or None) — NO model needed. Assert the default (Null) reproduces today's behavior. (The logic heart, fully testable offline.)
3. **`EmbeddingRunbookSelector`:** the real adapter (lazy sentence-transformers load, symptom-embedding cache, cosine similarity, threshold, fail-safe). Add `sentence-transformers` to the `ml` extra. Tests: the closed-catalog + fail-safe (monkeypatched model-load-raises → None) with a fake; ONE real-model semantic test (the keyword-miss → scale-service case) gated/marked so CI without the model still passes, OR using a stub-embedding injection so the cosine logic is tested deterministically. Confirm the slim-boundary (action importable without transformers).
4. **Docs (+ optional UI chip):** an RCA/README section (rules → semantic fallback → gap; how to enable; the honest "semantic retrieval among vetted playbooks, not an LLM choosing" framing); optionally the incident-panel semantic-match chip. Final gates + slim-boundary.

Rationale: contract + symptoms first (green), then the augment-logic with a fake selector (the real decision flow, fully offline-testable), then the embedding adapter (the model integration, with the cosine logic tested deterministically via stub embeddings + one real-model check), then docs. The safety invariant (ranks only registered playbooks; default reproduces today) is provable at every step.
