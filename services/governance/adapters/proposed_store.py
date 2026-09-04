from __future__ import annotations

from common.contracts import ProposedPlaybook, ProposedPlaybookStatus


class InMemoryProposedPlaybookStore:
    def __init__(self) -> None:
        self._by_id: dict[str, ProposedPlaybook] = {}

    def add(self, proposal: ProposedPlaybook) -> None:
        self._by_id[proposal.id] = proposal

    def get(self, proposal_id: str) -> ProposedPlaybook | None:
        return self._by_id.get(proposal_id)

    def list(self, status: ProposedPlaybookStatus | None = None) -> list[ProposedPlaybook]:
        items = list(self._by_id.values())
        if status is not None:
            items = [p for p in items if p.status == status]
        return items

    def set_status(
        self, proposal_id: str, status: ProposedPlaybookStatus, decided_by: str
    ) -> ProposedPlaybook | None:
        cur = self._by_id.get(proposal_id)
        if cur is None:
            return None
        updated = cur.model_copy(update={"status": status, "decided_by": decided_by})
        self._by_id[proposal_id] = updated
        return updated

    def clear(self) -> None:
        self._by_id.clear()
