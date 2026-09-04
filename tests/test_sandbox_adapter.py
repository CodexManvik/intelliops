from datetime import UTC, datetime

from common.contracts import (
    PreflightResult,
    RemediationOutcome,
    RemediationPlan,
    RemediationResult,
    RemediationStep,
    RemediationTarget,
    Situation,
    SituationStatus,
)
from services.action.adapters.sandbox import NullSandbox


def _situation() -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


def _plan() -> RemediationPlan:
    return RemediationPlan(
        target=RemediationTarget(namespace="intelliops", deployment="demo-app"),
        steps=[RemediationStep(action="restart")],
    )


def test_null_sandbox_passes_through():
    result = NullSandbox().rehearse(_situation(), _plan())
    assert isinstance(result, PreflightResult)
    assert result.passed is True
    assert result.mode == "off"
    assert result.sandbox_namespace is None


def test_preflight_is_additive_and_optional():
    # Existing constructions must still work with no preflight supplied.
    outcome = RemediationOutcome(
        situation_id="sit-1",
        playbook_id="pb-1",
        result=RemediationResult.SUCCESS,
        health_after="healthy",
        ts=datetime.now(UTC),
    )
    assert outcome.preflight is None


# --- NamespaceCloneSandbox: fail-safety + teardown (the k8s rehearsal) --------
#
# The live happy path runs only on the user's kind cluster (a documented MANUAL
# step, Task 5) and is NOT unit-tested end-to-end. The two properties that MUST
# hold regardless of cluster state — and that are cheap to test with fakes — are
# locked here: (1) rehearse never propagates an exception (always returns a
# PreflightResult with passed=False on error), and (2) the throwaway namespace is
# always torn down in the finally, even when the clone body fails partway.


class _FakeApiRaises:
    """Every read/create raises — proves the sandbox never propagates."""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise RuntimeError("k8s down")

        return _boom


def test_namespace_clone_sandbox_is_fail_safe(monkeypatch):
    from services.action.adapters import sandbox as sb

    # Force the adapter's k8s client construction to yield a raising fake.
    monkeypatch.setattr(
        sb, "_load_k8s", lambda: (_FakeApiRaises(), _FakeApiRaises()), raising=False
    )
    s = sb.NamespaceCloneSandbox("intelliops")
    result = s.rehearse(_situation(), _plan())
    assert result.passed is False
    assert result.mode == "k8s"
    assert "error" in result.detail.lower()


class _AppsV1ReadRaises:
    """AppsV1 whose deployment read raises — models a mid-clone k8s failure.

    Every other attribute is a no-op callable, so the ONLY failure comes from the
    clone body's first real call (reading the target Deployment). That failure
    must still leave the finally-block teardown intact.
    """

    def read_namespaced_deployment(self, *a, **k):
        raise RuntimeError("read failed mid-clone")

    def __getattr__(self, name):
        return lambda *a, **k: None


class _RecordingCoreV1:
    """CoreV1 that records delete_namespace calls; everything else is a no-op."""

    def __init__(self):
        self.deleted = []

    def delete_namespace(self, name, *a, **k):
        self.deleted.append(name)

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_namespace_clone_sandbox_tears_down_on_failure_path(monkeypatch):
    from services.action.adapters import sandbox as sb

    core = _RecordingCoreV1()
    # AppsV1 read raises (failure mid-clone); CoreV1 delete_namespace records.
    monkeypatch.setattr(sb, "_load_k8s", lambda: (_AppsV1ReadRaises(), core), raising=False)
    s = sb.NamespaceCloneSandbox("intelliops")
    result = s.rehearse(_situation(), _plan())

    assert result.passed is False
    # The finally block must have attempted teardown of the throwaway namespace.
    assert len(core.deleted) == 1
    torn_down = core.deleted[0]
    assert torn_down.startswith("intelliops-sandbox-")
    # And the audited namespace on the result is the one that was torn down.
    assert result.sandbox_namespace == torn_down


class _AppsV1HappyPath:
    def read_namespaced_deployment(self, *a, **k):
        return object()

    def create_namespaced_deployment(self, *a, **k):
        return None


class _CoreV1HappyPath:
    def __init__(self):
        self.deleted = []

    def create_namespace(self, *a, **k):
        return None

    def delete_namespace(self, name, *a, **k):
        self.deleted.append(name)


def test_namespace_clone_sandbox_fails_when_apply_returns_false(monkeypatch):
    from services.action.adapters import sandbox as sb

    apps = _AppsV1HappyPath()
    core = _CoreV1HappyPath()
    monkeypatch.setattr(sb, "_load_k8s", lambda: (apps, core), raising=False)
    monkeypatch.setattr(sb, "_strip_deployment", lambda dep, ns: dep, raising=False)
    monkeypatch.setattr(sb, "_namespace_body", lambda ns: object(), raising=False)
    monkeypatch.setattr(sb, "_referenced_config_map_names", lambda dep: [], raising=False)
    monkeypatch.setattr(
        sb.NamespaceCloneSandbox,
        "_clone_service_best_effort",
        lambda self, core_v1, dep_name, sandbox_ns: None,
        raising=False,
    )
    monkeypatch.setattr(
        sb.NamespaceCloneSandbox,
        "_clone_config_maps_best_effort",
        lambda self, core_v1, source_dep, sandbox_ns: None,
        raising=False,
    )

    health_checks = []

    class _HealthChecker:
        def __init__(self, *a, **k):
            pass

        def check(self, *a, **k):
            health_checks.append("check")
            return True

    monkeypatch.setattr(sb, "KubernetesHealthChecker", _HealthChecker, raising=False)

    class _Remediator:
        def __init__(self, *a, **k):
            pass

        def execute(self, plan):
            return False

    monkeypatch.setattr(sb, "KubernetesRemediator", _Remediator, raising=False)

    result = sb.NamespaceCloneSandbox("intelliops").rehearse(_situation(), _plan())
    assert result.passed is False
    assert result.detail == "sandbox: clone demo-app remediation apply failed"
    assert result.mode == "k8s"
    assert result.sandbox_namespace is not None
    assert len(health_checks) == 1
    assert core.deleted == [result.sandbox_namespace]
