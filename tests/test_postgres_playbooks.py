import pytest

from common.contracts import HitlMode, Playbook, RemediationStep
from services.governance.adapters.playbook_store import PostgresPlaybookStore


def _pb(pid="restart-pod", mode=HitlMode.HITL):
    return Playbook(
        id=pid,
        name="Restart",
        match_rule="x",
        steps=[RemediationStep(action="restart")],
        hitl_mode=mode,
        reversible=True,
        rollback_steps=[RemediationStep(action="restart")],
    )


@pytest.mark.postgres
def test_register_get_list(clean_db):
    s = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    s.register(_pb("my-pb"))
    got = s.get("my-pb")
    assert got is not None and got.id == "my-pb" and got.steps[0].action == "restart"
    assert "my-pb" in {p.id for p in s.list()}


@pytest.mark.postgres
def test_register_twice_upserts(clean_db):
    s = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    s.register(_pb("g", mode=HitlMode.HITL))
    s.register(_pb("g", mode=HitlMode.AUTO))  # graduation: same id, new mode
    assert s.get("g").hitl_mode == HitlMode.AUTO
    assert len([p for p in s.list() if p.id == "g"]) == 1  # not duplicated


@pytest.mark.postgres
def test_seed_playbooks_present_on_fresh_store(clean_db):
    s = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    ids = {p.id for p in s.list()}
    assert "restart-pod" in ids and "scale-service" in ids


@pytest.mark.postgres
def test_seed_on_init_does_not_revert_graduation(clean_db):
    # First store seeds + graduates a playbook to AUTO
    s1 = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    s1.register(_pb("restart-pod", mode=HitlMode.AUTO))  # graduation
    assert s1.get("restart-pod").hitl_mode == HitlMode.AUTO
    # A second store constructed against the SAME db (simulates a restart) re-seeds.
    s2 = PostgresPlaybookStore(clean_db, seed_path="deploy/playbooks")
    # The graduated AUTO must survive — seed-on-init must NOT reset it to the seed's HITL.
    assert s2.get("restart-pod").hitl_mode == HitlMode.AUTO
