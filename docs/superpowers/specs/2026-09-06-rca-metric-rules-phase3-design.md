# Richer RCA rules + AI-computed confidence — Design Spec (Metrics Phase 3)

**Date:** 2026-09-06
**Owner:** Manvik
**Status:** design (architectural — adds metric-family RCA rules and, crucially, lets the embedding model COMPUTE each hypothesis's confidence — the fit of a candidate runbook to the incident — replacing hardcoded confidence constants where available, so the AI chooses which runbook helps while deterministic ranking + HITL still make the binding call). **Phase 3 of a 4-phase metrics arc** (P1: rich metrics ✅ #38 · P2: detection policy ✅ #39 · **P3: RCA rules + AI confidence** · P4: per-metric health verification).

**Depends on:** Phases 1 (#38) + 2 (#39) merged (the metric families + their detection), and PR D (#36, the `EmbeddingRunbookSelector` whose per-candidate cosine scoring this phase reuses). Branch off master.

## The problem

RCA's `rank_hypotheses` (`services/rca/rank.py`) has only **three** rules, keyed on cpu/error/deploy name-tokens, with **hardcoded** confidence constants (deploy 0.8 → `rollback-deploy`; saturation 0.6 → `scale-service`; log/error 0.5 → `restart-pod`; else fallback 0.2, no runbook). Two problems now that Phases 1–2 added ~11 metric families:

1. **New families aren't mapped or are MIS-mapped.** `memory_leak` hits the saturation rule (`_SATURATION_TOKENS` includes "mem") → `scale-service` — but scaling doesn't fix a leak (the new pods leak too); **restart** is the right fix. `latency`/`queue_depth`/`db_pool`/`request_rate` have no rule at all → they fall to the semantic selector or the gap.
2. **The confidences are hand-tuned constants**, not a measure of how well a runbook actually fits this incident. The user wants **the AI to compute the confidence** so the *fit* — not a magic number — chooses the runbook.

## Goal

A **two-layer diagnosis**: keyword rules propose *candidate* hypotheses (keeping the candidate set closed + auditable, and adding the missing metric families), and the **embedding model computes each candidate's confidence** by scoring how well the incident's symptoms match that candidate runbook's `symptoms` field (reusing PR D's cosine machinery). So:
- The **AI (embedding) computes the confidence** that ranks the runbooks — the user's ask — but it only ever *scores among the vetted, rule-proposed candidates*; it never invents an action, and deterministic ranking + the HITL gate + the denylist + the sandbox all still apply. This is the same "retrieval, not an LLM deciding" principle as PR D, now applied to *confidence* rather than just fallback selection.
- The **memory-leak mis-mapping is resolved by the embeddings, not a new hand-tuned constant**: a memory-leak incident scores higher against `restart-pod`'s symptom text ("crash loops, wedged process, leak, recycle") than against `scale-service`'s ("saturation, capacity") — so the right runbook wins on *fit*, automatically.
- **Off by default is byte-identical:** when the embedding confidence is unavailable (`RUNBOOK_SELECTOR_MODE=off`, the default, or the model errors), the rules fall back to their existing hardcoded confidences — today's behavior exactly. The base suite + Phase-1 diagnoses are unchanged.

## Key decisions (locked with the user)

1. **AI-computed confidence = embedding fit score** (not an LLM, not hardcoded). Reuse `EmbeddingRunbookSelector`'s per-candidate cosine similarity: for a hypothesis whose candidate runbook is `R`, the confidence is the cosine similarity of (the incident's symptoms + hypothesis description) against `R`'s `symptoms` text. Chosen over an LLM scorer (non-deterministic, needs a model, adds a hot-path call) and over the hybrid (more moving parts). Deterministic given the vectors, offline, no hallucination, already in the codebase.
2. **Keyword rules stay as the candidate layer**, extended for the new families. Rules PROPOSE (situation → candidate runbook + a base/fallback confidence); the embedding SCORES. This keeps the candidate set closed (a rule only ever proposes one of the vetted runbooks) and auditable, while the AI decides the ranking via fit.
3. **memory-leak → restart-pod, ranked above saturation.** A dedicated rule proposes `restart-pod` for a ramping/high memory signal; combined with the embedding fit (memory-leak matches restart's symptoms better than scale's), restart wins. Refine `_SATURATION_TOKENS` so a pure memory metric doesn't ALSO fire the scale candidate (or let the embedding-scored restart candidate outrank it) — the tests pin the resulting diagnosis.
4. **New family rules → best-fit among the existing 3 runbooks; NO new runbooks in Phase 3.** Add candidate rules: `latency`/`queue_depth`/`request_rate` → `scale-service` (capacity/contention); `db_pool` exhaustion → `restart-pod` (recycle connections); `memory` (leak) → `restart-pod`. The closed set stays restart-pod/scale-service/rollback-deploy — Phase 3 is pure *routing*, no new remediation/safety surface (the tier-2 vocab + denylist of #34 already govern what actions are safe). Adding a new runbook later is just a new playbook with a `symptoms` field — the embedding picks it up for free.
4b. **Off-by-default byte-identical:** with the embedding unavailable, each rule uses its existing hardcoded confidence, preserving today's deploy>saturation>error ordering and every existing RCA diagnosis. The AI confidence is an *opt-in refinement*, never a regression.

## Non-goals / constraints

- **No LLM in RCA ranking.** The AI confidence is the embedding fit score (decision 1). The LLM's roles stay as before (advisory explanations, AI-drafted runbooks).
- **No new runbooks / no new remediation actions** (decision 4). Phase 3 changes *diagnosis routing + confidence*, not the action catalog. The closed `RemediationStep` Literal + denylist + sandbox are untouched.
- **The candidate set stays closed + rule-proposed.** The embedding scores among rule-proposed candidates (and, as today via `select_runbook`, can still surface a semantic match when no rule fires) — it never fabricates a runbook id. `store.get(id) is not None` is still enforced.
- **Off is byte-identical.** With `RUNBOOK_SELECTOR_MODE=off` (default) or on any embedding error, rules use their hardcoded confidences → the ~529 base suite + every Phase-1 fault diagnosis is unchanged. The embedding-confidence path is opt-in and fail-safe (never raises out of ranking).
- **Preserve the load-bearing cross-metric invariant's effect.** An `error`/`dependency_outage` incident (cpu held flat by Phase 1) must still diagnose to `restart-pod`, NOT `scale-service`. With embeddings on, this must hold a fortiori (error symptoms match restart best). Tests assert it both off and on.
- **Slim-boundary holds.** The embedding scoring already lives behind the lazy `sentence_transformers` import in `runbook_selector.py`; RCA already carries that. No new heavy-dep leak; no change to the slim services.
- **Config-switched via the EXISTING knob.** Reuse `RUNBOOK_SELECTOR_MODE` (off/embedding) + `RUNBOOK_SELECTOR_MODEL`/`_THRESHOLD` from PR D — the AI-confidence path is on when the embedding selector is on. No new config unless a threshold specifically for confidence-vs-selection is needed (decide in the plan; default: reuse).

## Global Constraints

- **Gates:** `uv run pytest -m "not postgres and not kafka"` green (~529 base + new tests); `ruff check .` + `ruff format --check .` clean.
- **Slim-boundary CI green:** no heavy-dep leak into slim services (the embedding import stays lazy in `runbook_selector.py`; RCA is where it lives).
- **Env:** `uv sync --extra ml --extra k8s` at setup.
- **Safety invariant:** rules only ever propose one of the vetted runbooks; the embedding only scores among candidates (never fabricates); `store.get(id)` enforced; off/error → hardcoded confidences (byte-identical); the error→restart invariant holds off AND on; no new runbook/action/gate.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **git:** branch `feat/rca-metric-rules-phase3` off master. PR; user merges. Never merge to master.
- **Shared files:** `services/rca/rank.py` (the rules + confidence), `services/rca/adapters/runbook_selector.py` (expose a per-candidate `score`), `services/rca/consumer.py` (thread the selector into ranking — it already threads it into `select_runbook`), `playbooks/*.yaml` + `deploy/playbooks/*.yaml` (sharpen the `symptoms` text so the fit scoring is accurate), and the RCA tests.

---

## Design

### 1. Expose per-candidate scoring on the selector (`services/rca/adapters/runbook_selector.py`)

`EmbeddingRunbookSelector.select` already encodes the query + each candidate's symptoms and computes cosine similarity, returning the best `(id, score)`. Refactor to expose the per-candidate score so RCA ranking can ask "how well does runbook R fit this incident?":
```python
def score(self, situation, hypothesis, playbook) -> float | None:
    """Cosine fit of (incident symptoms + hypothesis) against `playbook.symptoms`.
    None when unavailable (no symptoms, model error) — caller falls back to the
    hypothesis's rule confidence. Never raises."""
```
`select` is re-expressed in terms of `score` (score every candidate, take the argmax ≥ threshold) so there's one scoring path. `NullRunbookSelector.score` returns `None`. Fail-safe: any error → `None`.

### 2. Rules propose candidates; embedding computes confidence (`services/rca/rank.py`)

`rank_hypotheses` gains an optional `selector` (a `RunbookSelector`) — threaded from the consumer, defaulting to a null selector. Each rule still builds a `RootCauseHypothesis` with its candidate `suggested_runbook_id` and a **fallback confidence** (today's constant). Then, for each hypothesis with a runbook, **if the selector yields a fit score, that score becomes the confidence** (bounded/clamped); else the fallback constant stands. The list is ranked by the resulting confidence (the existing reliability boost still applies on top).

New/refined rules (each proposes a candidate + fallback confidence; the embedding refines):
- **deploy** → `rollback-deploy`, fallback 0.8 (unchanged).
- **memory ramp/high** (`memory_usage_mb` present + elevated) → `restart-pod`, fallback e.g. 0.65 (above saturation's 0.6 so restart wins even off). Refine `_SATURATION_TOKENS` so memory doesn't also fire the scale candidate, OR add memory as its own rule ranked above saturation.
- **saturation** (cpu/disk/saturation tokens, memory removed) → `scale-service`, fallback 0.6.
- **latency / queue_depth / request_rate** → `scale-service`, fallback e.g. 0.55.
- **db_pool exhaustion** (`db_pool_in_use`/`db_pool` tokens) → `restart-pod`, fallback e.g. 0.55.
- **log/error** → `restart-pod`, fallback 0.5 (unchanged; the error→restart invariant).
- **fallback** → no runbook, 0.2 (unchanged).

The embedding fit then re-scores: a memory-leak incident's symptoms match `restart-pod`'s `symptoms` text more than `scale-service`'s, so restart's confidence rises above scale's — resolving the mis-mapping by fit, not by a fragile constant. When off, the fallbacks preserve today's ordering.

### 3. Sharpen playbook `symptoms` (`playbooks/*.yaml` + `deploy/playbooks/*.yaml`)

For the embedding fit to route correctly, each runbook's `symptoms` must describe its incident shape crisply and distinctly:
- `restart-pod`: "crash loops, wedged/hung process, memory leak trending to OOM, database connection-pool exhaustion, elevated error rate, stuck workers — a process that must be recycled."
- `scale-service`: "CPU/resource saturation, high latency under load, growing queue depth, a traffic surge or capacity shortfall — more replicas needed."
- `rollback-deploy`: "a regression right after a recent deployment/release, a bad new version — revert to the prior revision."
(Add the memory-leak/db-pool language to restart, latency/queue/surge to scale — so the cosine fit lands each family on the intended runbook. Keep both playbook dirs in sync, additive, per the Phase-1 precedent.)

### 4. Wire the selector into ranking (`services/rca/consumer.py`)

`diagnose` already builds/threads the selector into `select_runbook`. Pass the same selector into `rank_hypotheses` so the confidence is embedding-computed. Default null selector → hardcoded confidences (off path unchanged). Record provenance (e.g. `confidence_source: "embedding" | "rule"` on the hypothesis evidence, mirroring `explanation_source`) so the operator sees whether a confidence was AI-computed or a rule default — honest.

### 5. No config change (reuse `RUNBOOK_SELECTOR_MODE`)

The AI-confidence path is active exactly when the embedding selector is (`RUNBOOK_SELECTOR_MODE=embedding` + `ml` extra). Off by default → hardcoded confidences. (If a distinct confidence threshold proves needed, add one in the plan; default is to reuse the selector's config.)

---

## Acceptance criteria

1. **Off is byte-identical:** with `RUNBOOK_SELECTOR_MODE=off` (default / null selector), `rank_hypotheses` produces the same hypotheses + confidences + ordering as today for the existing scenarios; the ~529 base suite + every Phase-1 fault diagnosis (saturation→scale, error→restart, deploy→rollback, latency+cpu→scale) is unchanged. Unit test: null selector → hypotheses match the pre-Phase-3 confidences.
2. **AI computes the confidence:** with an enabled selector (a fake-embedding one in tests), a hypothesis's confidence equals the embedding fit score (not the hardcoded constant). Unit test: a stub selector returning a known score per (hypothesis, runbook) makes that score the confidence.
3. **memory-leak → restart-pod (fit resolves the mis-mapping):** a memory-leak incident (`memory_usage_mb` elevated, cpu flat) diagnoses to `restart-pod`, NOT `scale-service` — both OFF (via the fallback 0.65 > 0.6 ordering) AND ON (via the embedding fit). Unit test both.
4. **New families map correctly:** `db_exhaustion` (`db_pool_in_use`→max) → `restart-pod`; `latency`/`queue_depth`/`request_rate` → `scale-service`; each with a hypothesis + candidate runbook. Unit tests per family (off path via the rule; on path confirms the fit doesn't invert it).
5. **error→restart invariant holds off AND on:** an `error`/`dependency_outage` incident (cpu flat) diagnoses to `restart-pod`, never `scale-service`, with the selector off and on. Unit test.
6. **Closed catalog + fail-safe:** the embedding only scores among rule-proposed/registered runbooks (never fabricates); any embedding error → the hypothesis keeps its rule confidence (never raises out of ranking). Unit test with a raising stub selector → falls back to rule confidences.
7. **Provenance visible:** a hypothesis records whether its confidence was `embedding`- or `rule`-sourced. Projection/UI shows it (or at least the evidence carries it) — honest, mirroring `explanation_source`.
8. **Gates green + slim-boundary:** ~529 + new tests; ruff clean; slim-boundary CI green (embedding import stays lazy).

## Suggested task ordering (for the plan)

1. **Expose `score` on the selector:** refactor `EmbeddingRunbookSelector` to a per-candidate `score(situation, hypothesis, playbook) -> float | None`; re-express `select` in terms of it; `NullRunbookSelector.score -> None`; fail-safe. Unit tests (fake-encode: score is the cosine; null → None; error → None). (Pure, reuses PR D's machinery.)
2. **Refined + new rules in `rank_hypotheses` (rules propose; fallback confidences):** the memory→restart rule (ranked above saturation), db_pool→restart, latency/queue/request_rate→scale, saturation with memory removed. Add the optional `selector` param (default null). WITHOUT the embedding-confidence yet — just the candidate rules + fallback constants, and confirm the OFF path gives the intended diagnoses (memory→restart, db_pool→restart, error→restart, saturation→scale, latency→scale, deploy→rollback). Unit tests (AC 1, 3-off, 4-off, 5-off). (The candidate layer — fully deterministic, testable off.)
3. **Embedding-computed confidence:** in `rank_hypotheses`, when the selector yields a `score` for a hypothesis's runbook, use it as the confidence (clamped); else the fallback. Provenance (`confidence_source`). Unit tests with a fake/stub selector (AC 2, 3-on, 5-on, 6). (The AI-confidence layer.)
4. **Sharpen playbook symptoms + wire selector into ranking:** update `symptoms` in both playbook dirs so the fit routes each family correctly; thread the selector from `consumer.diagnose` into `rank_hypotheses`; provenance to the projection/UI (best-effort). Unit tests + confirm the live wiring. (AC 4-on, 7.)
5. **Docs:** ADR-028 (RCA rules + AI-computed confidence via embedding fit — the "AI chooses the runbook by fit, deterministic ranking + HITL still decide" framing); update flow.md §5.3 (the two-layer diagnosis); note in MERIDIAN.md how the new families route; README ADR count 27→28. Commit spec + plan. Final gates.

Rationale: expose the score first (reuse), then the candidate rules with fallback constants (deterministic, off-path provable), then the embedding confidence (the AI layer, stub-tested), then symptoms+wiring (routing accuracy), then docs. The off-is-byte-identical + error→restart invariants are provable at every step; the AI computes the confidence but only ever ranks vetted, rule-proposed candidates.
