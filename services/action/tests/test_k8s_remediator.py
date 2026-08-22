from common.contracts import RemediationPlan, RemediationStep, RemediationTarget
from services.action.adapters import k8s_remediator as k8s_remediator_module
from services.action.adapters.k8s_remediator import KubernetesRemediator


class FakeApiException(Exception):
    pass


class FakeAppsV1:
    def __init__(self, replicas=1, fail_on=None):
        self.calls = []
        self._replicas = replicas
        self._fail_on = fail_on  # method name to raise on

    def _maybe_fail(self, name):
        if self._fail_on == name:
            raise FakeApiException("boom")

    def read_namespaced_deployment(self, name, namespace):
        self._maybe_fail("read")
        self.calls.append(("read", name, namespace))

        class _Scale:
            spec = type("S", (), {"replicas": self._replicas})()

        return _Scale()

    def patch_namespaced_deployment(self, name, namespace, body):
        self._maybe_fail("patch")
        self.calls.append(("patch", name, namespace, body))

    def patch_namespaced_deployment_scale(self, name, namespace, body):
        self._maybe_fail("scale")
        self.calls.append(("scale", name, namespace, body))


def _plan(*steps, rollback=()):
    return RemediationPlan(
        target=RemediationTarget(namespace="ns", deployment="demo-app"),
        steps=list(steps),
        rollback_steps=list(rollback),
    )


def test_restart_patches_restartedat_annotation():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="restart"))) is True
    patch = next(c for c in api.calls if c[0] == "patch")
    body = patch[3]
    # the restartedAt annotation is set in the pod template
    ann = body["spec"]["template"]["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/restartedAt" in ann


def test_scale_adds_replicas_to_current():
    api = FakeAppsV1(replicas=1)
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="scale", replicas=2))) is True
    scale = next(c for c in api.calls if c[0] == "scale")
    assert scale[3]["spec"]["replicas"] == 3  # 1 + 2


def test_wait_is_a_noop():
    api = FakeAppsV1()
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="wait", note="x"))) is True
    assert api.calls == []  # nothing hit the API


def test_api_error_returns_false_never_raises():
    api = FakeAppsV1(fail_on="patch")
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="restart"))) is False


def test_rollback_runs_rollback_steps():
    api = FakeAppsV1(replicas=3)
    r = KubernetesRemediator("ns", apps_v1=api, exc_type=FakeApiException)
    assert r.rollback(_plan(rollback=[RemediationStep(action="scale", replicas=-2)])) is True
    scale = next(c for c in api.calls if c[0] == "scale")
    assert scale[3]["spec"]["replicas"] == 1  # 3 + (-2)


def test_client_acquisition_failure_returns_false_never_raises(monkeypatch):
    # Simulates a real kubernetes.config.load_kube_config() failure (e.g. missing
    # or unreadable kubeconfig, which raises ConfigException — NOT ApiException).
    # This must be caught by the fail-closed path just like an ApiException is,
    # not escape execute()/rollback() as a raw exception (ADR-007).
    class FakeConfigException(Exception):
        pass

    def _boom():
        raise FakeConfigException("kubeconfig not found")

    monkeypatch.setattr(k8s_remediator_module, "_default_apps_v1", _boom)

    # apps_v1=None forces _api() to lazily call _default_apps_v1() inside _run(),
    # which is exactly the code path that must be inside the guarded try/except.
    r = KubernetesRemediator("ns", apps_v1=None, exc_type=FakeApiException)
    assert r.execute(_plan(RemediationStep(action="restart"))) is False
