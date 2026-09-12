"""The ops-proxy fault/clear URL switches between compose service DNS and the
in-cluster NodePort (via host.docker.internal) based on meridian_ops_target_mode."""

from __future__ import annotations

from services.meridian.gateway.ops_target import ops_target_url


def test_compose_mode_targets_service_dns():
    assert ops_target_url("aggregation", "admin/fault", "compose") == (
        "http://meridian-aggregation:8000/admin/fault"
    )


def test_k8s_mode_targets_nodeport_via_host_docker_internal():
    # gateway 30808, validation 30811, aggregation 30812, reporting 30813
    assert ops_target_url("aggregation", "admin/fault", "k8s") == (
        "http://host.docker.internal:30812/admin/fault"
    )
    assert ops_target_url("validation", "admin/clear", "k8s") == (
        "http://host.docker.internal:30811/admin/clear"
    )
    assert ops_target_url("gateway", "admin/fault", "k8s") == (
        "http://host.docker.internal:30808/admin/fault"
    )
    assert ops_target_url("reporting", "admin/clear", "k8s") == (
        "http://host.docker.internal:30813/admin/clear"
    )
