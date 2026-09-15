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
    detection_policy = "on"
    detection_ratio_threshold = 0.02
    detection_saturation_ratio_threshold = 0.80
    detection_saturation_percent_threshold = 90.0
    detection_latency_ceiling_ms = 500.0
    correlation_z_threshold = 3.0
    health_check_timeout_seconds = 90.0


def test_dry_run_defaults():
    assert isinstance(_make_remediator(_S()), DryRunRemediator)
    assert isinstance(_make_health_checker(_S()), AlwaysHealthyChecker)


def test_k8s_mode_selects_k8s_adapters():
    s = _S()
    s.remediator_mode = "k8s"
    s.health_check_mode = "k8s"
    assert isinstance(_make_remediator(s), KubernetesRemediator)
    checker = _make_health_checker(s)
    assert isinstance(checker, KubernetesHealthChecker)
    # The deadline must come from settings. It was hardcoded to 30.0, which
    # exactly equals the default terminationGracePeriodSeconds, so the
    # "readyReplicas == replicas" signal (the surge pod counts until the OLD pod
    # is gone) could not go true in time and every successful restart was
    # reported as a rollback.
    assert checker._timeout == 90.0
