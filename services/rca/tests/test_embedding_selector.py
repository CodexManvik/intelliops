"""EmbeddingRunbookSelector: cosine-similarity semantic match over playbook
`symptoms`, tested deterministically via a fake `_encode` — no real
sentence-transformers model is loaded by this suite (see task-3 brief:
loading a real model in CI is slow/heavy and may not be available offline).
"""

import os
from datetime import UTC, datetime

import numpy as np
import pytest

from common.contracts import (
    HitlMode,
    Playbook,
    RemediationStep,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.governance.adapters.playbook_store import InMemoryPlaybookStore
from services.rca.adapters import runbook_selector as rs

TS = datetime(2026, 9, 5, tzinfo=UTC)


def _situation(metric_name="container_working_set_bytes"):
    ev = TelemetryEvent(
        source="p",
        kind=TelemetryKind.METRIC,
        name=metric_name,
        value=90.0,
        labels={"service": "web"},
        ts=TS,
        fingerprint="fp",
    )
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[ev],
        severity="high",
        first_seen=TS,
        last_seen=TS,
        signature="sig-1",
    )


def _store():
    s = InMemoryPlaybookStore()
    s.register(
        Playbook(
            id="scale-service",
            name="Scale",
            match_rule="*",
            steps=[RemediationStep(action="scale", replicas=1)],
            hitl_mode=HitlMode.HITL,
            symptoms="high CPU or memory saturation",
        )
    )
    return s


def _fake_encode(texts):
    # deterministic toy embeddings: "saturation-ish" -> [1,0]; "deploy-ish" ->
    # [0,1]; else -> [0.5,0.5]
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
    monkeypatch.setattr(
        rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False
    )
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    # a "thrashing under load" situation (no literal saturation token) matches
    # the scale-service symptoms via the fake encoder's word-bucket vectors
    sit = _situation("container_working_set_bytes")
    hyp = RootCauseHypothesis(
        situation_id="sit-1", description="service thrashing under load", confidence=0.2
    )
    hit = sel.select(sit, hyp, _store())
    assert hit is not None
    assert hit[0] == "scale-service"
    assert hit[1] >= 0.8


def test_embedding_selector_below_threshold_returns_none(monkeypatch):
    monkeypatch.setattr(
        rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False
    )
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    # a "deploy rollout" situation is orthogonal to the scale-service
    # symptoms under the fake encoder ([0,1] vs [1,0] -> cosine 0.0)
    sit = _situation("weird_metric_xyz")
    hyp = RootCauseHypothesis(
        situation_id="sit-1", description="new release rollout in progress", confidence=0.2
    )
    assert sel.select(sit, hyp, _store()) is None


def test_embedding_selector_closed_catalog_empty_store(monkeypatch):
    monkeypatch.setattr(
        rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False
    )
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    hyp = RootCauseHypothesis(situation_id="sit-1", description="anything", confidence=0.2)
    assert sel.select(_situation(), hyp, InMemoryPlaybookStore()) is None  # no candidates -> None


def test_embedding_selector_closed_catalog_ignores_empty_symptoms(monkeypatch):
    # a registered playbook with no `symptoms` text is not a candidate — the
    # selector never fabricates a match against it.
    monkeypatch.setattr(
        rs.EmbeddingRunbookSelector, "_encode", staticmethod(_fake_encode), raising=False
    )
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    store = InMemoryPlaybookStore()
    store.register(
        Playbook(
            id="no-symptoms",
            name="No symptoms",
            match_rule="*",
            steps=[RemediationStep(action="scale", replicas=1)],
            hitl_mode=HitlMode.HITL,
        )
    )
    hyp = RootCauseHypothesis(
        situation_id="sit-1", description="high cpu saturation", confidence=0.2
    )
    assert sel.select(_situation(), hyp, store) is None


def test_embedding_selector_fail_safe_on_model_error(monkeypatch):
    def _boom(texts):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(rs.EmbeddingRunbookSelector, "_encode", staticmethod(_boom), raising=False)
    sel = rs.EmbeddingRunbookSelector(threshold=0.8)
    hyp = RootCauseHypothesis(situation_id="sit-1", description="x", confidence=0.2)
    assert sel.select(_situation(), hyp, _store()) is None  # error -> None, never raises


def test_embedding_selector_default_construction_does_not_load_model():
    # constructing the selector must not itself trigger a model load / import
    # (lazy import lives inside _encode, called only from select()).
    sel = rs.EmbeddingRunbookSelector()
    assert sel._model_name == "all-MiniLM-L6-v2"
    assert sel._threshold == 0.45


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


# Real-model integration test: exercises the actual sentence-transformers
# encode + cosine path end to end (the keyword-miss -> scale-service case
# that the rule-based path can't catch). Gated on BOTH the package being
# importable AND an explicit opt-in env var, so the default suite (even in
# an env where `uv sync --extra ml` happened to install the package) never
# triggers a model download — only a deliberate `RUN_EMBEDDING_MODEL_TESTS=1`
# run does, matching how this repo gates the other heavy/live paths
# (postgres/kafka markers, live k8s/LLM tests).
@pytest.mark.skipif(
    not _sentence_transformers_available() or not os.environ.get("RUN_EMBEDDING_MODEL_TESTS"),
    reason="requires sentence-transformers installed AND RUN_EMBEDDING_MODEL_TESTS=1 (downloads/loads a real model)",
)
def test_embedding_selector_real_model_matches_keyword_miss():
    # "thrashing under load" has no literal saturation/cpu/memory token, so
    # the keyword rules in rank.py miss it entirely -- only the semantic
    # path can route it to scale-service.
    sel = rs.EmbeddingRunbookSelector(threshold=0.3)
    sit = _situation("container_working_set_bytes")
    hyp = RootCauseHypothesis(
        situation_id="sit-1", description="service thrashing under sustained load", confidence=0.2
    )
    hit = sel.select(sit, hyp, _store())
    assert hit is not None
    assert hit[0] == "scale-service"
    assert hit[1] >= 0.3
