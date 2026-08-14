"""Evidence-based playbook graduation policy (ADR-008).

A playbook graduates hitl→auto only after a clean track record: at least
`min_successes` successful remediations and ZERO failures or rollbacks in the
observed window. Conservative by design — automation scope expands on evidence,
not optimism. feedback proposes; governance promotes under RBAC."""

from __future__ import annotations

from common.contracts import RemediationResult, TrainingRecord


def playbook_stats(records: list[TrainingRecord], playbook_id: str) -> dict:
    successes = failures = rollbacks = 0
    for r in records:
        if r.playbook_id != playbook_id:
            continue
        if r.result == RemediationResult.SUCCESS:
            successes += 1
        elif r.result == RemediationResult.FAILURE:
            failures += 1
        elif r.result == RemediationResult.ROLLED_BACK:
            rollbacks += 1
    return {"successes": successes, "failures": failures, "rollbacks": rollbacks}


def should_graduate(stats: dict, min_successes: int) -> bool:
    return (stats["successes"] >= min_successes
            and stats["failures"] == 0
            and stats["rollbacks"] == 0)
