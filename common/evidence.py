"""What counts as evidence that a fix works - shared by correlation and action.

Two decisions rest on a remediation track record:

- correlation marks a situation `handling="quiet"` when its signature has been
  fixed reliably, and
- action lets a quiet situation's playbook run WITHOUT a human approval, but
  only after re-checking that track record for that exact playbook.

Both must judge evidence the same way, or correlation would keep asking for
quiet handling that action keeps refusing. Hence one module.

Only real runs count. DryRunRemediator + AlwaysHealthyChecker report success
unconditionally, and records written before TrainingRecord carried `mode` (None)
cannot be told apart from dry runs - so neither is evidence that a change to a
real cluster is safe to make unattended.
"""

from __future__ import annotations

from dataclasses import dataclass

_NOT_REAL = frozenset({None, "dry_run", "none"})


def _field(record, name):
    return record.get(name) if isinstance(record, dict) else getattr(record, name, None)


def is_real(record) -> bool:
    return _field(record, "mode") not in _NOT_REAL


def real_records(records) -> list:
    return [r for r in records if is_real(r)]


@dataclass(frozen=True)
class TrackRecord:
    worked: int
    total: int

    @property
    def ratio(self) -> float:
        return self.worked / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.worked}/{self.total} real runs worked"


def track_record(records, signature: str, playbook_id: str | None = None) -> TrackRecord:
    """Real outcomes for `signature`, optionally narrowed to one playbook."""
    worked = total = 0
    for r in records:
        if not is_real(r) or _field(r, "signature") != signature:
            continue
        if playbook_id is not None and _field(r, "playbook_id") != playbook_id:
            continue
        total += 1
        if _field(r, "worked"):
            worked += 1
    return TrackRecord(worked, total)


def quiet_eligible(
    records, signature: str, playbook_id: str, threshold: float, min_samples: int
) -> tuple[bool, TrackRecord]:
    """May this playbook run on this signature without a human approval?"""
    record = track_record(records, signature, playbook_id)
    ok = record.total >= max(1, min_samples) and record.ratio >= threshold
    return ok, record
