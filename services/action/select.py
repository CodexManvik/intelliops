"""Select the remediation playbook for a diagnosed situation.

Connects "what's wrong" (the RCA-suggested runbook id) to "what to do" (the
registered playbook). Returns None when there is no suggestion or the id is
unknown — the caller emits a skipped outcome (see flow.md 5.4)."""

from __future__ import annotations

from common.contracts import DiagnosedSituation, Playbook
from common.interfaces import PlaybookStore


def select_playbook(diagnosed: DiagnosedSituation, store: PlaybookStore) -> Playbook | None:
    runbook_id = diagnosed.suggested_runbook_id
    if runbook_id is None:
        return None
    return store.get(runbook_id)
