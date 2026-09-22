"""The console's built-in identity must be allowed to do what the console asks of it.

The console sends its operator name as `decided_by` / `requested_by` on every
approval, rejection and AI-draft request. When that default named nobody in the
RBAC policy, every one of those clicks came back 403 from a stock build.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.governance.rbac import RbacPolicy


def _console_default_operator() -> str:
    src = Path("frontend/src/data/api.ts").read_text(encoding="utf-8")
    m = re.search(r'VITE_OPERATOR_NAME\s*\|\|\s*"([^"]+)"', src)
    assert m, "could not find the console's default OPERATOR_NAME in api.ts"
    return m.group(1)


def _dockerfile_default_operator() -> str:
    src = Path("deploy/Dockerfile.frontend").read_text(encoding="utf-8")
    m = re.search(r"ARG VITE_OPERATOR_NAME=(\S+)", src)
    assert m, "deploy/Dockerfile.frontend must pin VITE_OPERATOR_NAME"
    return m.group(1)


def test_console_default_operator_can_decide_and_draft():
    policy = RbacPolicy.from_file("policies/rbac_policy.yaml")
    for who in {_console_default_operator(), _dockerfile_default_operator()}:
        # decide_approval / approve_proposed: approve on the specific playbook
        assert policy.check(who, "approve", "playbook:restart-pod"), who
        # reject_proposed
        assert policy.check(who, "reject", "playbook:restart-pod"), who
        # propose / draft-async gate on the wildcard resource
        assert policy.check(who, "approve", "playbook:*"), who
