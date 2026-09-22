"""Evidence-based playbook graduation policy (ADR-008).

A playbook graduates hitl→auto only after a clean track record: at least
`min_successes` successful remediations and ZERO failures or rollbacks in the
observed window. Conservative by design — automation scope expands on evidence,
not optimism. feedback proposes; governance promotes under RBAC."""

from __future__ import annotations

from common.contracts import RemediationResult, TrainingRecord

# Modes in which nothing real was touched. DryRunRemediator + AlwaysHealthyChecker
# report SUCCESS unconditionally, so three approved dry runs used to graduate a
# playbook to AUTO - and flipping REMEDIATOR_MODE=k8s afterwards meant real
# changes with no human in the loop, on evidence from a simulation.
_SIMULATED_MODES = frozenset({"dry_run"})


def playbook_stats(
    records: list[TrainingRecord], playbook_id: str, count_simulated: bool = False
) -> dict:
    """Per-playbook evidence. A simulated success counts toward nothing unless
    `count_simulated` - it proves the gates ran, not that the fix works. Records
    from before `mode` existed (None) keep counting as they always did."""
    successes = failures = rollbacks = 0
    for r in records:
        if r.playbook_id != playbook_id:
            continue
        if r.result == RemediationResult.SUCCESS:
            if r.mode in _SIMULATED_MODES and not count_simulated:
                continue
            successes += 1
        elif r.result == RemediationResult.FAILURE:
            failures += 1
        elif r.result == RemediationResult.ROLLED_BACK:
            rollbacks += 1
        elif r.result == RemediationResult.ESCALATED:
            # Counted in no bucket, on purpose: nothing was attempted, so it is no
            # evidence for or against this playbook. Scoring it as a failure would
            # disqualify it forever (should_graduate demands failures == 0).
            continue
    return {"successes": successes, "failures": failures, "rollbacks": rollbacks}


def should_graduate(stats: dict, min_successes: int) -> bool:
    return (
        stats["successes"] >= min_successes and stats["failures"] == 0 and stats["rollbacks"] == 0
    )
