"""Outcome-derived metrics for feedback-service.

Computes only what RemediationOutcomes actually support — success/rollback/
failure rates, counts by result, and per-signature worked/total. True MTTR/MTTD
need end-to-end detection→resolution timestamps not yet threaded, so they are
NOT fabricated; the `note` states what's deferred (see flow.md 5.6)."""

from __future__ import annotations

from common.contracts import RemediationResult, TrainingRecord

_NOTE = (
    "MTTR/MTTD require end-to-end detection→resolution timestamps not yet "
    "threaded; reported metrics are outcome-derived."
)


def compute_metrics(records: list[TrainingRecord]) -> dict:
    total = len(records)
    # Derived from the enum so a new member can never KeyError this counter.
    by_result = {r.value: 0 for r in RemediationResult}
    by_signature: dict[str, dict[str, int]] = {}
    for r in records:
        by_result[r.result.value] += 1
        sig = by_signature.setdefault(r.signature, {"worked": 0, "total": 0})
        sig["total"] += 1
        if r.worked:
            sig["worked"] += 1
    # An escalation means nothing was attempted, so it can neither succeed nor
    # fail — counting it would understate the success rate of work we did do.
    attempted = total - by_result["escalated"]

    def rate(n: int) -> float:
        # All three rates share the attempted-only denominator: mixing bases would
        # make success/rollback/failure fail to reconcile against each other.
        return n / attempted if attempted else 0.0

    return {
        "total_outcomes": total,
        "success_rate": rate(by_result["success"]),
        "rollback_rate": rate(by_result["rolled_back"]),
        "failure_rate": rate(by_result["failure"]),
        "by_result": by_result,
        "by_signature": by_signature,
        "note": _NOTE,
    }
