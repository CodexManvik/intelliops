from common.contracts import HitlMode, Playbook, RemediationStep
from common.interfaces import PlaybookStore
from services.governance.adapters.playbook_store import (
    FilePlaybookStore,
    InMemoryPlaybookStore,
)


def _playbook(pid="restart-pod"):
    return Playbook(
        id=pid,
        name="Restart Pod",
        match_rule="signature == 'x'",
        steps=[RemediationStep(action="restart")],
        hitl_mode=HitlMode.HITL,
        reversible=True,
        rollback_steps=[RemediationStep(action="restart")],
    )


def test_inmemory_store_satisfies_protocol():
    assert isinstance(InMemoryPlaybookStore(), PlaybookStore)


def test_inmemory_register_get_list():
    store = InMemoryPlaybookStore()
    store.register(_playbook())
    assert store.get("restart-pod").name == "Restart Pod"
    assert store.get("missing") is None
    assert [p.id for p in store.list()] == ["restart-pod"]


def test_file_store_persists_and_reloads(tmp_path):
    store = FilePlaybookStore(str(tmp_path))
    store.register(_playbook("scale-service"))
    # a fresh store over the same dir sees the registered playbook
    reloaded = FilePlaybookStore(str(tmp_path))
    assert reloaded.get("scale-service") is not None
    assert reloaded.get("scale-service").hitl_mode == HitlMode.HITL


def test_seed_playbooks_load():
    from services.governance.adapters.playbook_store import load_seed_playbooks

    seeds = load_seed_playbooks("playbooks")
    ids = {p.id for p in seeds}
    assert {"rollback-deploy", "scale-service", "restart-pod"} <= ids
