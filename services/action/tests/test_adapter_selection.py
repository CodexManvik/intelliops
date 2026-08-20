from services.action.adapters.health import AlwaysHealthyChecker
from services.action.adapters.k8s_health import KubernetesHealthChecker
from services.action.adapters.k8s_remediator import KubernetesRemediator
from services.action.adapters.remediator import DryRunRemediator
from services.action.app import _make_health_checker, _make_remediator


class _S:
    remediator_mode = "dry_run"
    health_check_mode = "always"
    k8s_namespace = "intelliops-demo"
    prometheus_url = "http://localhost:9090"


def test_dry_run_defaults():
    assert isinstance(_make_remediator(_S()), DryRunRemediator)
    assert isinstance(_make_health_checker(_S()), AlwaysHealthyChecker)


def test_k8s_mode_selects_k8s_adapters():
    s = _S()
    s.remediator_mode = "k8s"
    s.health_check_mode = "k8s"
    assert isinstance(_make_remediator(s), KubernetesRemediator)
    assert isinstance(_make_health_checker(s), KubernetesHealthChecker)
