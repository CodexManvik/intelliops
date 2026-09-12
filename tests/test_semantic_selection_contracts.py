import yaml

from common.contracts import HitlMode, Playbook, RemediationStep


def test_playbook_symptoms_optional_and_additive():
    pb = Playbook(
        id="p",
        name="n",
        match_rule="*",
        steps=[RemediationStep(action="restart")],
        hitl_mode=HitlMode.HITL,
    )
    assert pb.symptoms is None  # default
    pb2 = Playbook(
        id="p2",
        name="n",
        match_rule="*",
        steps=[],
        hitl_mode=HitlMode.HITL,
        symptoms="high CPU, saturation",
    )
    assert pb2.symptoms == "high CPU, saturation"


def test_seed_playbooks_have_symptoms():
    # The 3 seed playbooks have non-empty symptom text. Read the YAML directly
    # rather than via load_seed_playbooks(path) — that loader takes a required
    # seed-directory path (services/governance/adapters/playbook_store.py), so
    # there is no path-free call to make here; reading the files is simpler and
    # avoids picking a path. Checked against both the top-level `playbooks/`
    # directory and `deploy/playbooks/` (the one actually baked into the
    # container image and used at runtime — see deploy/Dockerfile), since the
    # two currently hold separately-maintained copies of the same 3 playbooks.
    for base in ("playbooks", "deploy/playbooks"):
        for pid in ("scale-service", "restart-pod", "rollback-deploy"):
            with open(f"{base}/{pid}.yaml", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            assert data["symptoms"] and len(data["symptoms"]) > 10
