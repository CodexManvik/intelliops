"""Unit tests for the shared Meridian fault mechanism (services/meridian/common.py).

No FastAPI app / TestClient here — pure state-machine tests, so no Prometheus
gauge registry is touched and these can run alongside any other test module.
"""

from __future__ import annotations

from services.meridian.common import CPU_BROKEN, CPU_HEALTHY, FaultSpec, MeridianState


def _apply(scenario: str, **kw) -> MeridianState:
    """Build a fresh MeridianState, apply one FaultSpec, sample it, return it.

    `now` (default 0.0) is forwarded to sample(); everything else in `kw`
    (magnitude, duration_seconds) is forwarded to FaultSpec.
    """
    now = kw.pop("now", 0.0)
    st = MeridianState()
    st.apply(FaultSpec(type=scenario, **kw))
    st.sample(now=now)
    return st


def test_starts_healthy():
    state = MeridianState()
    assert state.cpu == CPU_HEALTHY
    assert state.error_rate == 0.0
    assert state.latency_ms == 0.0
    assert state.unhealthy is False


def test_saturation_fault_spikes_cpu():
    state = MeridianState()
    state.apply(FaultSpec(type="saturation"))
    assert state.cpu == CPU_BROKEN


def test_error_fault_sets_error_rate_and_keeps_cpu_at_baseline():
    state = MeridianState()
    state.apply(FaultSpec(type="error", magnitude=0.5))
    assert state.error_rate == 0.5
    # CRITICAL: cpu must stay at baseline so RCA maps to restart-pod, not
    # scale-service (see task-1-brief.md).
    assert state.cpu == CPU_HEALTHY


def test_latency_fault_sets_latency_and_cpu():
    state = MeridianState()
    state.apply(FaultSpec(type="latency", magnitude=1.0))
    assert state.latency_ms == 200.0
    assert state.cpu == CPU_BROKEN


def test_latency_fault_scales_with_magnitude():
    state = MeridianState()
    state.apply(FaultSpec(type="latency", magnitude=2.0))
    assert state.latency_ms == 400.0


def test_crash_fault_marks_unhealthy():
    state = MeridianState()
    state.apply(FaultSpec(type="crash"))
    assert state.unhealthy is True


def test_clear_resets_all_fields():
    state = MeridianState()
    state.apply(FaultSpec(type="saturation"))
    state.apply(FaultSpec(type="error", magnitude=0.9))
    state.unhealthy = True
    state.clear()
    assert state.cpu == CPU_HEALTHY
    assert state.error_rate == 0.0
    assert state.latency_ms == 0.0
    assert state.unhealthy is False


def test_saturation_magnitude_caps_at_100():
    state = MeridianState()
    state.apply(FaultSpec(type="saturation", magnitude=2.0))
    assert state.cpu == 100.0


def test_error_magnitude_caps_at_1():
    state = MeridianState()
    state.apply(FaultSpec(type="error", magnitude=5.0))
    assert state.error_rate == 1.0


# --- Metrics Phase 1 Task 2: 8 pinned scenario -> metric-cluster profiles ---
#
# THE load-bearing invariant under test throughout this section: a scenario
# moves ONLY the metrics that incident would realistically move; every other
# metric stays at its MeridianState() baseline. In particular "error" and
# "dependency_outage" MUST leave cpu at CPU_HEALTHY -- see common.py's module
# docstring for why (two anomalous signals would make RCA's scale-service
# outrank restart-pod, misdiagnosing an error incident as a capacity one).


def test_saturation_moves_cpu_cluster_only():
    baseline = MeridianState()
    st = _apply("saturation")
    assert st.cpu > CPU_HEALTHY
    assert st.saturation > baseline.saturation
    assert st.queue_depth > baseline.queue_depth
    # off-cluster metrics stay at baseline
    assert st.error_rate == 0.0
    assert st.latency_p50_ms == baseline.latency_p50_ms
    assert st.latency_p99_ms == baseline.latency_p99_ms
    assert st.request_rate == baseline.request_rate
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent
    assert st.unhealthy is False


def test_latency_moves_latency_cluster_and_cpu():
    # NOTE: the legacy "latency" fault (predates Task 2) hard-spikes cpu to
    # CPU_BROKEN exactly -- test_latency_fault_sets_latency_and_cpu below (and
    # test_metrics.py::test_latency_fault_sets_cpu_to_92_too) pin that value,
    # so this scenario keeps cpu at a full spike rather than a mild bump; the
    # new latency_p50/p99_ms + queue_depth cluster is what Task 2 adds.
    baseline = MeridianState()
    st = _apply("latency")
    assert st.latency_p50_ms > baseline.latency_p50_ms
    assert st.latency_p99_ms > baseline.latency_p99_ms
    assert st.queue_depth > baseline.queue_depth
    assert st.cpu == CPU_BROKEN
    # off-cluster metrics stay at baseline
    assert st.error_rate == 0.0
    assert st.request_rate == baseline.request_rate
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.saturation == baseline.saturation
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_error_keeps_cpu_and_latency_at_baseline():
    """THE load-bearing invariant: an error incident must not also look like
    a capacity incident, or RCA misdiagnoses it (see module docstring)."""
    baseline = MeridianState()
    st = _apply("error", magnitude=0.5)
    assert st.error_rate > 0
    assert st.cpu == CPU_HEALTHY  # cpu must NOT move
    assert st.latency_p50_ms == baseline.latency_p50_ms
    assert st.latency_p99_ms == baseline.latency_p99_ms
    # off-cluster metrics stay at baseline
    assert st.request_rate == baseline.request_rate
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.saturation == baseline.saturation
    assert st.queue_depth == baseline.queue_depth
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_memory_leak_ramps_over_duration():
    st = _apply("memory_leak", magnitude=1.0, duration_seconds=100.0, now=0.0)
    m0 = st.memory_usage_mb
    st.sample(now=50.0)
    m50 = st.memory_usage_mb
    st.sample(now=100.0)
    m100 = st.memory_usage_mb
    assert m0 < m50 < m100  # strictly climbing, two samples apart in time
    baseline = MeridianState()
    # off-cluster metrics stay at baseline throughout the ramp
    assert st.cpu == CPU_HEALTHY
    assert st.error_rate == 0.0
    assert st.latency_p50_ms == baseline.latency_p50_ms
    assert st.latency_p99_ms == baseline.latency_p99_ms
    assert st.request_rate == baseline.request_rate
    assert st.saturation == baseline.saturation
    assert st.queue_depth == baseline.queue_depth
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_traffic_surge_moves_rate_and_capacity_cluster():
    baseline = MeridianState()
    st = _apply("traffic_surge")
    assert st.request_rate > baseline.request_rate
    assert st.cpu > CPU_HEALTHY
    assert st.saturation > baseline.saturation
    assert st.queue_depth > baseline.queue_depth
    # off-cluster metrics stay at baseline
    assert st.error_rate == 0.0
    assert st.latency_p50_ms == baseline.latency_p50_ms
    assert st.latency_p99_ms == baseline.latency_p99_ms
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_dependency_outage_moves_errors_and_latency_not_cpu():
    """THE load-bearing invariant, second scenario: same reasoning as
    test_error_keeps_cpu_and_latency_at_baseline -- a downstream-dependency
    outage looks like errors + tail latency, NOT a local capacity problem."""
    baseline = MeridianState()
    st = _apply("dependency_outage")
    assert st.error_rate > 0
    assert st.latency_p99_ms > baseline.latency_p99_ms
    assert st.cpu == CPU_HEALTHY  # cpu must NOT move
    # off-cluster metrics stay at baseline
    assert st.request_rate == baseline.request_rate
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.saturation == baseline.saturation
    assert st.queue_depth == baseline.queue_depth
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_db_exhaustion_saturates_pool_and_lifts_latency():
    baseline = MeridianState()
    st = _apply("db_exhaustion")
    assert st.db_pool_in_use >= st.db_pool_max
    assert st.latency_p99_ms > baseline.latency_p99_ms
    # off-cluster metrics stay at baseline
    assert st.error_rate == 0.0
    assert st.cpu == CPU_HEALTHY
    assert st.request_rate == baseline.request_rate
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.saturation == baseline.saturation
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_crash_sets_unhealthy_and_moves_nothing_else():
    baseline = MeridianState()
    st = _apply("crash")
    assert st.unhealthy is True
    # detection-only: every metric field stays at baseline
    assert st.cpu == CPU_HEALTHY
    assert st.error_rate == 0.0
    assert st.latency_p50_ms == baseline.latency_p50_ms
    assert st.latency_p99_ms == baseline.latency_p99_ms
    assert st.request_rate == baseline.request_rate
    assert st.memory_usage_mb == baseline.memory_usage_mb
    assert st.saturation == baseline.saturation
    assert st.queue_depth == baseline.queue_depth
    assert st.db_pool_in_use == baseline.db_pool_in_use
    assert st.disk_usage_percent == baseline.disk_usage_percent


def test_legacy_scenario_types_still_recognized():
    for t in ("saturation", "error", "latency", "crash"):
        _apply(t)  # must not raise; scenario recognized


def test_all_8_scenarios_recognized_without_raising():
    for t in (
        "saturation",
        "latency",
        "error",
        "memory_leak",
        "traffic_surge",
        "dependency_outage",
        "db_exhaustion",
        "crash",
    ):
        _apply(t)
