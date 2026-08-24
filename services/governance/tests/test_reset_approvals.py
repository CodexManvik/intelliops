from fastapi.testclient import TestClient

from common.contracts import ApprovalRequest
from services.governance.adapters.approval_store import InMemoryApprovalStore


def _client():
    from services.governance.app import app

    app.state.approval_store = InMemoryApprovalStore()
    return app, TestClient(app)


def test_reset_approvals_clears_in_memory_store():
    app, c = _client()
    app.state.approval_store.create(
        ApprovalRequest(
            id="a1", situation_id="s1", playbook_id="restart-pod", requested_by="action-service"
        )
    )
    assert app.state.approval_store.get("a1") is not None
    r = c.post("/reset-approvals")
    assert r.status_code == 200 and r.json() == {"reset": True}
    assert app.state.approval_store.get("a1") is None


def test_reset_approvals_deletes_db_rows_in_postgres_mode():
    app, c = _client()
    executed = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, stmt):
            executed.append(str(stmt))

    class _Engine:
        def begin(self):
            return _Conn()

    app.state.db_engine = _Engine()
    c.post("/reset-approvals")
    assert any("approvals" in s.lower() for s in executed)
    del app.state.db_engine
