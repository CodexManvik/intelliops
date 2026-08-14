"""Label a RemediationOutcome as a TrainingRecord — the raw material of the loop.

The signature is derived from the situation id (the "sit-" prefix convention
set by RiverCorrelator.correlate), so no frozen contract needs a new field.
`worked` is True only for a clean success (a rollback or failure did not fix it)."""

from __future__ import annotations

from common.contracts import RemediationOutcome, RemediationResult, TrainingRecord


def signature_from_situation_id(situation_id: str) -> str:
    return situation_id.removeprefix("sit-")


def label_outcome(outcome: RemediationOutcome) -> TrainingRecord:
    return TrainingRecord(
        situation_id=outcome.situation_id,
        signature=signature_from_situation_id(outcome.situation_id),
        playbook_id=outcome.playbook_id,
        result=outcome.result,
        worked=outcome.result == RemediationResult.SUCCESS,
        ts=outcome.ts,
    )
