from services.governance.adapters.playbook_store import load_seed_playbooks


def test_seeded_playbooks_parse_as_structured():
    pbs = {p.id: p for p in load_seed_playbooks("deploy/playbooks")}
    assert "restart-pod" in pbs and "scale-service" in pbs
    rp = pbs["restart-pod"]
    assert rp.steps[0].action == "restart"       # structured, not a string
    ss = pbs["scale-service"]
    assert any(s.action == "scale" and s.replicas for s in ss.steps)
