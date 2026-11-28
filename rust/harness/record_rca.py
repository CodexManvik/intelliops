"""Record Python RCA: diagnosis with a stubbed selector, and real embedding
scores (the latter needs the model, so run it in the full image).

    uv run python rust/harness/record_rca.py rules
    docker run --rm -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo \
        intelliops:full python rust/harness/record_rca.py embeddings
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from common.contracts import (
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.governance.adapters.playbook_store import (
    InMemoryPlaybookStore,
    load_seed_playbooks,
)
from services.rca.adapters.explanation_provider import TemplateExplanationProvider
from services.rca.consumer import diagnose

APP_CASES = Path(__file__).resolve().parents[1] / "app-cases"
NAMES = [
    "cpu_usage",
    "memory_usage_mb",
    "service_up",
    "latency_p99_ms",
    "queue_depth",
    "request_rate",
    "meridian_error_rate",
    "db_pool_in_use",
    "disk_usage_percent",
    "saturation",
    "http.server.request.duration",
    "tls_handshake_failures",
    "Weird",
]


def situation(rng: random.Random, names=None) -> Situation:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    services = rng.choice([["gateway"], ["validation", "gateway"], [None]])
    members = [
        TelemetryEvent(
            source="s",
            kind=rng.choice([TelemetryKind.METRIC] * 9 + [TelemetryKind.LOG]),
            name=name,
            value=1.0,
            labels={} if (s := rng.choice(services)) is None else {"service": s},
            ts=now,
            fingerprint=name,
        )
        for name in (names or rng.sample(NAMES, rng.choice([1, 1, 2, 3, 5])))
    ]
    sig = f"sig{rng.randrange(5)}"
    return Situation(
        id=f"sit-{sig}",
        status=SituationStatus.DETECTED,
        member_events=members,
        severity="low",
        first_seen=now,
        last_seen=now,
        signature=sig,
    )


class Context:
    def __init__(self, deploys):
        self.deploys = deploys

    def recent_deploys(self):
        return self.deploys

    def topology_for(self, labels):
        return {}

    def config_changes(self):
        return []


class Selector:
    """Scores and picks from a fixed table, standing in for the model."""

    def __init__(self, scores, pick):
        self.scores = scores
        self.pick = pick

    def score(self, situation, hypothesis, playbook):
        return self.scores.get(playbook.id)

    def select(self, situation, hypothesis, store):
        return tuple(self.pick) if self.pick else None


def rules() -> None:
    playbooks = load_seed_playbooks("deploy/playbooks")
    cases = []
    for seed in range(400):
        rng = random.Random(seed)
        store = InMemoryPlaybookStore()
        stored = rng.sample(playbooks, rng.choice([3, 3, 3, 2, 0]))
        for p in stored:
            store.register(p)
        deploys = rng.choice(
            [[], [{"service": "gateway", "version": "v2"}], [{"service": "other", "version": "1"}]]
        )
        s = situation(rng)
        scores = rng.choice(
            [
                None,
                {},
                {p.id: rng.choice([-0.2, 0.1, 0.5, 0.9, 1.4]) for p in playbooks},
            ]
        )
        pick = rng.choice([None, ["restart-pod", 0.61], ["nope", 0.9], ["scale-service", 0.5]])
        reliability = rng.choice(
            [None, {p.id: rng.choice([0.0, 0.5, 1.0, 1.7]) for p in playbooks}]
        )
        provider = None
        if reliability is not None:
            provider = lambda sig, runbook=None, table=reliability: table.get(runbook, 0.0)
        out = diagnose(
            s,
            Context(deploys),
            store,
            TemplateExplanationProvider(),
            provider,
            None if scores is None else Selector(scores, pick),
        )
        cases.append(
            {
                "situation": json.loads(s.model_dump_json()),
                "playbooks": sorted(p.id for p in stored),
                "deploys": deploys,
                "scores": scores,
                "pick": pick,
                "reliability": reliability,
                "diagnosed": json.loads(out.model_dump_json()),
            }
        )
    (APP_CASES / "rca.json").write_text(
        json.dumps(cases, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(len(cases), "diagnoses")


def embeddings() -> None:
    from sentence_transformers import SentenceTransformer

    from services.rca.adapters.runbook_selector import EmbeddingRunbookSelector

    playbooks = load_seed_playbooks("deploy/playbooks")
    selector = EmbeddingRunbookSelector()
    model = SentenceTransformer("all-MiniLM-L6-v2")
    texts = [p.symptoms for p in playbooks] + ["", "a", "Weird Üñíçödé 📈 text, with: punctuation!"]
    texts.append("long " * 700)
    cases = []
    for seed in range(40):
        rng = random.Random(seed)
        s = situation(rng)
        h = RootCauseHypothesis(
            situation_id=s.id,
            description=rng.choice(
                [
                    "root cause undetermined from available signals",
                    "service is down — the process stopped serving",
                    "latency/queueing under load — capacity contention",
                    "a bad release",
                ]
            ),
            confidence=0.2,
        )
        texts.append(selector._query_text(s, h))
        cases.append(
            {
                "situation": json.loads(s.model_dump_json()),
                "hypothesis": h.description,
                "scores": {p.id: selector.score(s, h, p) for p in playbooks},
            }
        )
    vectors = model.encode(texts).tolist()
    out = {"texts": texts, "vectors": [[round(x, 6) for x in v] for v in vectors], "cases": cases}
    (APP_CASES / "embeddings.json").write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(len(texts), "vectors,", len(cases), "scored incidents")


if __name__ == "__main__":
    {"rules": rules, "embeddings": embeddings}[sys.argv[1]]()
