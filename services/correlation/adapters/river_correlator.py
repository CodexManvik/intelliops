"""Online anomaly detection + time/label correlation.

detect() maintains a per-metric online z-score baseline (river.stats). An
event scores high when its value is many std devs from that metric's running
mean. correlate() collapses a set of anomalous events into one Situation with
a stable signature so recurring storms are recognizable (see flow.md 5.2).

A per-metric warm-up gate suppresses scoring until the baseline has seen
`warmup_samples` observations, because river.stats.Var is unreliable early on
(a normal value can score a huge z). This greatly reduces — but does not fully
eliminate — cold-start false positives: the running variance keeps settling for
a while past the gate, so a marginal crossing just beyond warmup_samples is
still possible. Callers that need zero spurious startup anomalies should also
gate downstream (e.g. discard the first correlation window after a cold start).

NOTE (river 0.25): stats objects update in place via .update(v) and read via
.get(); they are not chained.
"""

from __future__ import annotations

import hashlib

from river import stats

from common.contracts import Situation, SituationStatus, TelemetryEvent


class RiverCorrelator:
    def __init__(self, z_threshold: float = 3.0, warmup_samples: int = 50) -> None:
        self._z_threshold = z_threshold
        self._warmup_samples = warmup_samples
        self._mean: dict[str, stats.Mean] = {}
        self._var: dict[str, stats.Var] = {}
        self._count: dict[str, int] = {}
        self._reliability: dict[str, float] = {}

    def detect(self, event: TelemetryEvent) -> float:
        if event.value is None:
            return 0.0
        name = event.name
        mean = self._mean.setdefault(name, stats.Mean())
        var = self._var.setdefault(name, stats.Var())
        seen = self._count.get(name, 0)
        # Score against the CURRENT baseline before folding this value in.
        m = mean.get()
        sd = var.get() ** 0.5
        # Warm-up gate: river.stats.Var is unstable for the first few samples,
        # so a normal value can score a huge z. Until the metric's baseline has
        # seen `warmup_samples` observations we keep learning but never flag —
        # otherwise a cold-started service emits spurious anomalies on startup.
        if seen < self._warmup_samples or sd == 0:
            score = 0.0
        else:
            score = abs(event.value - m) / sd
        mean.update(event.value)
        var.update(event.value)
        self._count[name] = seen + 1
        return score

    def is_anomaly(self, event: TelemetryEvent) -> bool:
        return self.detect(event) > self._z_threshold

    def snapshot(self) -> list[dict]:
        """Per-metric baseline as plain scalars (see tests/test_baseline_codec)."""
        out: list[dict] = []
        # Copy the items first: the consumer thread's detect() can setdefault a
        # new metric into _mean/_var mid-snapshot (detect() runs outside the
        # engine lock), and iterating a live dict during that resize raises
        # RuntimeError. A snapshotted key list is immune; a metric added after
        # the copy simply lands in the next snapshot.
        for name, mean in list(self._mean.items()):
            var = self._var[name]
            out.append(
                {
                    "metric_name": name,
                    "n": var.mean.n,
                    "mean": mean.get(),
                    "variance": var.get(),
                    "count": self._count.get(name, 0),
                }
            )
        return out

    def load(self, rows: list[dict]) -> None:
        """Rebuild _mean/_var/_count from persisted scalars via river's _from_state."""
        for r in rows:
            n = int(r["n"])
            self._mean[r["metric_name"]] = stats.Mean._from_state(n, r["mean"])
            self._var[r["metric_name"]] = stats.Var._from_state(n, r["mean"], r["variance"], ddof=1)
            self._count[r["metric_name"]] = int(r["count"])

    def correlate(self, events: list[TelemetryEvent], severity: str = "low") -> Situation:
        if not events:
            raise ValueError("cannot correlate an empty event list")
        signature = self._signature(events)
        return Situation(
            id="sit-" + signature,
            status=SituationStatus.DETECTED,
            member_events=list(events),
            severity=severity,
            first_seen=min(e.ts for e in events),
            last_seen=max(e.ts for e in events),
            signature=signature,
        )

    def retrain(self, training_data: list[dict]) -> None:
        # The closed loop: aggregate per-signature reliability from labeled
        # outcomes. A signature whose remediation reliably works becomes a
        # candidate for suppression (see should_suppress); one that fails stays
        # sensitive. Recomputes from the given data each call.
        worked: dict[str, int] = {}
        total: dict[str, int] = {}
        for record in training_data:
            sig = record["signature"]
            total[sig] = total.get(sig, 0) + 1
            if record.get("worked"):
                worked[sig] = worked.get(sig, 0) + 1
        self._reliability = {sig: worked.get(sig, 0) / n for sig, n in total.items()}

    def reliability(self, signature: str) -> float:
        return self._reliability.get(signature, 0.0)

    def should_suppress(self, signature: str, threshold: float) -> bool:
        return self.reliability(signature) >= threshold

    def _severity_band(self, score: float) -> str:
        if score >= 8:
            return "high"
        if score >= 5:
            return "medium"
        return "low"

    @staticmethod
    def _signature(events: list[TelemetryEvent]) -> str:
        joined = "|".join(sorted(e.fingerprint for e in events))
        return hashlib.sha1(joined.encode()).hexdigest()[:16]
