from services.governance.rbac import RbacPolicy

POLICY = {
    "roles": {
        "operator": [
            {"action": "enrich", "resource": "situation:*"},
            {"action": "diagnose", "resource": "situation:*"},
        ],
        "approver": [{"action": "approve", "resource": "playbook:*"}],
    },
    "actors": {
        "rca-service": ["operator"],
        "oncall-alice": ["operator", "approver"],
    },
}


def test_allows_action_within_role():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    assert p.check("rca-service", "diagnose", "situation:sit-1") is True


def test_denies_action_outside_role():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    # rca-service is only an operator, not an approver
    assert p.check("rca-service", "approve", "playbook:restart-pod") is False


def test_multi_role_actor_gets_union():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    assert p.check("oncall-alice", "approve", "playbook:restart-pod") is True
    assert p.check("oncall-alice", "diagnose", "situation:x") is True


def test_resource_glob_scoping():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    # approver may approve playbook:* but not situation:*
    assert p.check("oncall-alice", "approve", "situation:x") is False


def test_unknown_actor_denied():
    p = RbacPolicy(roles=POLICY["roles"], actors=POLICY["actors"])
    assert p.check("mystery", "diagnose", "situation:x") is False


def test_from_file_loads_yaml(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text(
        "roles:\n"
        "  operator:\n"
        "    - {action: diagnose, resource: 'situation:*'}\n"
        "actors:\n"
        "  rca-service: [operator]\n"
    )
    p = RbacPolicy.from_file(str(f))
    assert p.check("rca-service", "diagnose", "situation:abc") is True
