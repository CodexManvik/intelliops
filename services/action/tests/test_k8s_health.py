from datetime import UTC, datetime
from common.contracts import RemediationTarget, Situation, SituationStatus
from services.action.adapters.k8s_health import KubernetesHealthChecker

NOW = datetime(2026, 8, 18, tzinfo=UTC)

def _sit():
    return Situation(id="s", status=SituationStatus.ACTING, member_events=[], severity="high",
                     first_seen=NOW, last_seen=NOW, signature="sig")

def _tgt():
    return RemediationTarget(namespace="ns", deployment="demo-app")

class FakeExc(Exception): pass

class FakeApps:
    def __init__(self, ready, desired=1, fail=False):
        self._ready, self._desired, self._fail = ready, desired, fail
    def read_namespaced_deployment_status(self, name, namespace):
        if self._fail: raise FakeExc("boom")
        class _S: status = type("St", (), {"ready_replicas": self._ready, "replicas": self._desired})()
        return _S()

def _hc(apps, metric_ok, timeout=0.2):
    return KubernetesHealthChecker(apps_v1=apps, metric_healthy=lambda: metric_ok,
                                   timeout_seconds=timeout, poll_interval_seconds=0.0,
                                   exc_type=FakeExc)

def test_both_signals_green_returns_true():
    assert _hc(FakeApps(ready=1, desired=1), metric_ok=True).check(_sit(), _tgt()) is True

def test_pod_ready_but_metric_bad_times_out_false():
    assert _hc(FakeApps(ready=1, desired=1), metric_ok=False).check(_sit(), _tgt()) is False

def test_pod_not_ready_times_out_false():
    assert _hc(FakeApps(ready=0, desired=1), metric_ok=True).check(_sit(), _tgt()) is False

def test_api_error_does_not_raise_times_out_false():
    assert _hc(FakeApps(ready=1, fail=True), metric_ok=True).check(_sit(), _tgt()) is False
