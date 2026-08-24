"""Unit tests for the shared Meridian fault mechanism (services/meridian/common.py).

No FastAPI app / TestClient here — pure state-machine tests, so no Prometheus
gauge registry is touched and these can run alongside any other test module.
"""

from __future__ import annotations

from services.meridian.common import CPU_BROKEN, CPU_HEALTHY, FaultSpec, MeridianState


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
