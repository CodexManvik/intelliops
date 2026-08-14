"""Outcome-derived metrics for feedback-service.

Computes only what RemediationOutcomes actually support — success/rollback/
failure rates, counts by result, and per-signature worked/total. True MTTR/MTTD
need end-to-end detection→resolution timestamps not yet threaded, so they are
NOT fabricated; the `note` states what's deferred (see flow.md 5.6)."""

from __future__ import annotations

from common.contracts import TrainingRecord

_NOTE = ("MTTR/MTTD require end-to-end detection→resolution timestamps not yet "
         "threaded; reported metrics are outcome-derived.")


def compute_metrics(records: list[TrainingRecord]) -> dict:
    total = len(records)
    by_result = {"success": 0, "failure": 0, "rolled_back": 0}
    by_signature: dict[str, dict[str, int]] = {}
    for r in records:
        by_result[r.result.value] += 1
        sig = by_signature.setdefault(r.signature, {"worked": 0, "total": 0})
        sig["total"] += 1
        if r.worked:
            sig["worked"] += 1

    def rate(n: int) -> float:
        return n / total if total else 0.0

    return {
        "total_outcomes": total,
        "success_rate": rate(by_result["success"]),
        "rollback_rate": rate(by_result["rolled_back"]),
        "failure_rate": rate(by_result["failure"]),
        "by_result": by_result,
        "by_signature": by_signature,
        "note": _NOTE,
    }
