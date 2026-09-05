"""RunbookSelector implementations: pick a runbook by semantic similarity.

NullRunbookSelector is the CI-safe default (runbook_selector_mode="off"): it
selects nothing, so the runbook comes purely from the keyword rules (today's
behavior). EmbeddingRunbookSelector does the real semantic match: it embeds
the incident's symptoms + hypothesis and each registered playbook's
`symptoms`, then picks the best cosine-similarity match above a threshold.

EmbeddingRunbookSelector NEVER imports numpy or sentence-transformers at
module load time — both are imported lazily inside `_encode`/`select` so
that importing this module (and importing services.rca.app, which lazily
imports EmbeddingRunbookSelector only when runbook_selector_mode="embedding")
never pulls numpy into sys.modules. This keeps the CI slim-boundary check
green: services import cleanly in a venv without the `ml` extra."""

from __future__ import annotations

import logging

from common.contracts import RootCauseHypothesis, Situation
from common.interfaces import PlaybookStore

logger = logging.getLogger("intelliops.rca.runbook_selector")


class NullRunbookSelector:
    def select(
        self, situation: Situation, hypothesis: RootCauseHypothesis, store: PlaybookStore
    ) -> tuple[str, float] | None:
        return None


# Process-wide cache so repeated EmbeddingRunbookSelector construction (e.g.
# one per request in a naive caller) doesn't reload the sentence-transformers
# model each time. Keyed by model_name.
_MODEL_CACHE: dict = {}


class EmbeddingRunbookSelector:
    """Cosine-similarity runbook selection over playbook `symptoms` text.

    Ranks ONLY registered playbooks (never fabricates an id) and NEVER
    raises — any failure (empty store, all-empty symptoms, model load/encode
    error) is caught in `select` and returns None, so callers can always
    fall back to the rules-first behavior in `select_runbook`.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", threshold: float = 0.45):
        self._model_name = model_name
        self._threshold = threshold

    def _encode(self, texts):
        """Lazy-loads and caches a SentenceTransformer, then encodes `texts`.

        Overridden in tests (as a staticmethod, so it receives only `texts`)
        to inject deterministic fake vectors — no real model is loaded by
        the default test suite. `sentence_transformers` (which transitively
        imports numpy/scipy/sklearn) is imported HERE, not at module top
        level, to preserve the slim-boundary guarantee."""
        from sentence_transformers import SentenceTransformer

        model = _MODEL_CACHE.get(self._model_name)
        if model is None:
            model = SentenceTransformer(self._model_name)
            _MODEL_CACHE[self._model_name] = model
        return model.encode(texts)

    def _query_text(self, situation: Situation, hypothesis: RootCauseHypothesis) -> str:
        names = " ".join(e.name for e in situation.member_events)
        return f"{hypothesis.description}. signals: {names}".strip()

    def select(
        self, situation: Situation, hypothesis: RootCauseHypothesis, store: PlaybookStore
    ) -> tuple[str, float] | None:
        try:
            import numpy as np

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
            logger.info(
                "embedding runbook selection failed (%s); no semantic match",
                exc.__class__.__name__,
            )
            return None
