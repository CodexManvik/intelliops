from datetime import UTC, datetime
from typing import ClassVar

from common.contracts import (
    PreflightResult,
    RemediationOutcome,
    RemediationPlan,
    RemediationResult,
    RemediationStep,
    RemediationTarget,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.action.adapters.sandbox import NullSandbox
from services.correlation.detection_policy import DetectionPolicy


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


# --- NamespaceCloneSandbox: revision history seeding --------------------------


def test_seed_revision_history_copies_replicasets(monkeypatch):
    from services.action.adapters import sandbox as sb

    created_rs = []

    class _Apps:
        def read_namespaced_deployment(self, *a, **k):
            class _Selector:
                match_labels: ClassVar[dict] = {"app": "demo-app"}

            class _Spec:
                template = object()
                selector = _Selector()

            class _D:
                metadata = type("M", (), {"uid": "clone-uid", "name": "demo-app"})()
                spec = _Spec()

            return _D()

        def create_namespaced_deployment(self, *a, **k):
            return None

        def list_namespaced_replica_set(self, namespace, **k):
            class _OwnerRef:
                kind = "Deployment"
                name = "demo-app"

            class _RS:
                metadata = type(
                    "M",
                    (),
                    {
                        "annotations": {"deployment.kubernetes.io/revision": "2"},
                        "resource_version": "1",
                        "uid": "u",
                        "creation_timestamp": "t",
                        "owner_references": [_OwnerRef()],
                        "managed_fields": None,
                        "self_link": None,
                        "namespace": "intelliops",
                        "name": "demo-app-rs2",
                    },
                )()
                spec = type("S", (), {"template": object()})()
                status = object()

            return type("L", (), {"items": [_RS()]})()

        def create_namespaced_replica_set(self, namespace, body, **k):
            created_rs.append((namespace, body))

        # health-check path used by rehearse (rollout wait + post-fix)
        def read_namespaced_deployment_status(self, *a, **k):
            class _St:
                status = type("S", (), {"ready_replicas": 1, "replicas": 1})()

            return _St()

    class _Core:
        def __init__(self):
            self.deleted = []

        def create_namespace(self, *a, **k):
            return None

        def delete_namespace(self, name, *a, **k):
            self.deleted.append(name)

    apps, core = _Apps(), _Core()
    monkeypatch.setattr(sb, "_load_k8s", lambda: (apps, core), raising=False)
    monkeypatch.setattr(sb, "_strip_deployment", lambda dep, ns: dep, raising=False)
    monkeypatch.setattr(sb, "_namespace_body", lambda ns: object(), raising=False)
    monkeypatch.setattr(sb, "_referenced_config_map_names", lambda dep: [], raising=False)
    monkeypatch.setattr(
        sb.NamespaceCloneSandbox,
        "_clone_service_best_effort",
        lambda self, c, d, n: None,
        raising=False,
    )
    monkeypatch.setattr(
        sb.NamespaceCloneSandbox,
        "_clone_config_maps_best_effort",
        lambda self, c, s, n: None,
        raising=False,
    )

    class _HealthChecker:
        def __init__(self, *a, **k):
            pass

        def check(self, *a, **k):
            return True

    monkeypatch.setattr(sb, "KubernetesHealthChecker", _HealthChecker, raising=False)

    class _Remediator:
        def __init__(self, *a, **k):
            pass

        def execute(self, plan):
            return True

    monkeypatch.setattr(sb, "KubernetesRemediator", _Remediator, raising=False)

    # a rollback_to_revision plan triggers history seeding
    plan = RemediationPlan(
        target=RemediationTarget(namespace="intelliops", deployment="demo-app"),
        steps=[RemediationStep(action="rollback_to_revision", revision=2)],
    )
    result = sb.NamespaceCloneSandbox("intelliops").rehearse(_situation(), plan)
    # the RS was read and re-created in the sandbox namespace
    assert len(created_rs) == 1
    sandbox_ns_used, _ = created_rs[0]
    assert sandbox_ns_used.startswith("intelliops-sandbox-")  # created into the sandbox ns
    assert result.mode == "k8s"  # completed without raising
    assert core.deleted  # namespace torn down


def test_seed_revision_history_failure_is_swallowed(monkeypatch):
    from services.action.adapters import sandbox as sb

    class _Apps:
        def read_namespaced_deployment(self, *a, **k):
            class _Selector:
                match_labels: ClassVar[dict] = {"app": "demo-app"}

            class _Spec:
                template = object()
                selector = _Selector()

            class _D:
                metadata = type("M", (), {"uid": "clone-uid", "name": "demo-app"})()
                spec = _Spec()

            return _D()

        def create_namespaced_deployment(self, *a, **k):
            return None

        def list_namespaced_replica_set(self, *a, **k):
            raise RuntimeError("history read boom")  # seeding-specific failure

        def create_namespaced_replica_set(self, *a, **k):
            return None

        def read_namespaced_deployment_status(self, *a, **k):
            class _St:
                status = type("S", (), {"ready_replicas": 1, "replicas": 1})()

            return _St()

    class _Core:
        def __init__(self):
            self.deleted = []

        def create_namespace(self, *a, **k):
            return None

        def delete_namespace(self, name, *a, **k):
            self.deleted.append(name)

    apps, core = _Apps(), _Core()
    monkeypatch.setattr(sb, "_load_k8s", lambda: (apps, core), raising=False)
    monkeypatch.setattr(sb, "_strip_deployment", lambda dep, ns: dep, raising=False)
    monkeypatch.setattr(sb, "_namespace_body", lambda ns: object(), raising=False)
    monkeypatch.setattr(sb, "_referenced_config_map_names", lambda dep: [], raising=False)
    monkeypatch.setattr(
        sb.NamespaceCloneSandbox,
        "_clone_service_best_effort",
        lambda self, c, d, n: None,
        raising=False,
    )
    monkeypatch.setattr(
        sb.NamespaceCloneSandbox,
        "_clone_config_maps_best_effort",
        lambda self, c, s, n: None,
        raising=False,
    )

    class _HealthChecker:
        def __init__(self, *a, **k):
            pass

        def check(self, *a, **k):
            return True

    monkeypatch.setattr(sb, "KubernetesHealthChecker", _HealthChecker, raising=False)

    class _Remediator:
        def __init__(self, *a, **k):
            pass

        def execute(self, plan):
            return True

    monkeypatch.setattr(sb, "KubernetesRemediator", _Remediator, raising=False)

    # a NON-rollback plan (patch_probe) must still rehearse fine despite the
    # history-read failure — seeding is best-effort.
    plan = RemediationPlan(
        target=RemediationTarget(namespace="intelliops", deployment="demo-app"),
        steps=[RemediationStep(action="patch_probe", probe="liveness", period_seconds=10)],
    )
    result = sb.NamespaceCloneSandbox("intelliops").rehearse(_situation(), plan)
    assert isinstance(result, PreflightResult)  # never raised
    assert result.mode == "k8s"
    assert core.deleted  # torn down regardless


# --- NamespaceCloneSandbox: post-fix health check is per-metric (Metrics Phase 4) ---
#
# The sandbox's PRE-fix rollout-wait check stays pod-readiness only (it's waiting
# for the clone to come up, not verifying recovery). The POST-fix check, when the
# sandbox is constructed with a `policy`, must build a per-metric predicate the
# same way the live health path does (Task 2/3) — i.e. the post-fix
# KubernetesHealthChecker(...) call must receive `policy=` and `query_value=`.
# Because the sandbox is heavily k8s-mocked, the assertion spies on the
# KubernetesHealthChecker constructor within sandbox.py: two constructions happen
# per successful rehearse() (rollout-wait, then post-fix) — the LAST one seen is
# the post-fix call.


def _situation_firing_on(metric_name: str) -> Situation:
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        member_events=[
            TelemetryEvent(
                source="t",
                kind=TelemetryKind.METRIC,
                name=metric_name,
                value=300.0,
                ts=now,
                fingerprint="fp-1",
            )
        ],
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
        baseline={metric_name: {"mean": 200.0, "std": 20.0}},
    )


def _happy_path_mocks(monkeypatch, apps, core):
    """Shared plumbing for a full rehearse() happy path: mocks everything up to
    (and including) the two health checks, using the given apps_v1/core_v1 fakes.
    Modeled on test_namespace_clone_sandbox_fails_when_apply_returns_false above."""
    from services.action.adapters import sandbox as sb

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

    class _Remediator:
        def __init__(self, *a, **k):
            pass

        def execute(self, plan):
            return True  # fix "applies" cleanly so the flow reaches the post-fix check

    monkeypatch.setattr(sb, "KubernetesRemediator", _Remediator, raising=False)
    return sb


def test_sandbox_post_fix_check_is_per_metric(monkeypatch):
    import services.action.adapters.sandbox as sb

    seen = []
    real = sb.KubernetesHealthChecker

    class Spy(real):
        def __init__(self, *a, **k):
            seen.append(k)
            super().__init__(*a, **k)

        def check(self, situation, target):
            return True  # force a pass so the flow proceeds past both health checks

    monkeypatch.setattr(sb, "KubernetesHealthChecker", Spy)

    apps, core = _AppsV1HappyPath(), _CoreV1HappyPath()
    _happy_path_mocks(monkeypatch, apps, core)

    policy = DetectionPolicy(enabled=True)
    sandbox = sb.NamespaceCloneSandbox(
        "intelliops", prometheus_url="http://prom:9090", policy=policy, z_threshold=3.0
    )
    situation = _situation_firing_on("memory_usage_mb")
    result = sandbox.rehearse(situation, _plan())

    assert result.passed is True
    # Two KubernetesHealthChecker constructions: rollout-wait, then post-fix.
    assert len(seen) == 2
    rollout_kwargs, post_fix_kwargs = seen[0], seen[1]
    # PRE-fix rollout-wait stays pod-readiness only — no policy/query_value.
    assert rollout_kwargs.get("policy") is None
    assert rollout_kwargs.get("query_value") is None
    # POST-fix carries the per-metric predicate.
    assert post_fix_kwargs.get("policy") is policy
    assert post_fix_kwargs.get("query_value") is not None
    assert post_fix_kwargs.get("z_threshold") == 3.0


def test_sandbox_post_fix_check_is_pod_readiness_only_without_policy(monkeypatch):
    """Back-compat: a NamespaceCloneSandbox built with no policy (the default —
    e.g. existing direct constructions in tests, or sandbox_mode="k8s" before
    this wiring) must keep the post-fix check pod-readiness only, exactly as
    before Metrics Phase 4."""
    import services.action.adapters.sandbox as sb

    seen = []
    real = sb.KubernetesHealthChecker

    class Spy(real):
        def __init__(self, *a, **k):
            seen.append(k)
            super().__init__(*a, **k)

        def check(self, situation, target):
            return True

    monkeypatch.setattr(sb, "KubernetesHealthChecker", Spy)

    apps, core = _AppsV1HappyPath(), _CoreV1HappyPath()
    _happy_path_mocks(monkeypatch, apps, core)

    sandbox = sb.NamespaceCloneSandbox("intelliops")  # no policy — default
    result = sandbox.rehearse(_situation(), _plan())

    assert result.passed is True
    assert len(seen) == 2
    for kwargs in seen:
        assert kwargs.get("policy") is None
        assert kwargs.get("query_value") is None


# --- Serialization round-trip: V1OwnerReference must survive sanitize_for_serialization ----


def test_strip_replica_set_owner_ref_is_serializable():
    """_strip_replica_set must use V1OwnerReference (not an ad-hoc type()) so the
    kubernetes client's sanitize_for_serialization can handle the output.
    A plain type() object lacks openapi_types and would raise AttributeError."""
    from kubernetes import client as k8s_client
    from kubernetes.client import ApiClient

    from services.action.adapters.sandbox import _strip_replica_set

    # Build a minimal real RS object (use real k8s model objects so the client
    # can serialize the whole graph — including the OwnerReference we inject).
    rs = k8s_client.V1ReplicaSet(
        metadata=k8s_client.V1ObjectMeta(
            name="demo-app-abc",
            namespace="intelliops",
            resource_version="1",
            uid="rs-uid",
            annotations={"deployment.kubernetes.io/revision": "2"},
        ),
        spec=k8s_client.V1ReplicaSetSpec(
            selector=k8s_client.V1LabelSelector(match_labels={"app": "demo-app"}),
            template=k8s_client.V1PodTemplateSpec(
                metadata=k8s_client.V1ObjectMeta(labels={"app": "demo-app"}),
                spec=k8s_client.V1PodSpec(
                    containers=[k8s_client.V1Container(name="app", image="demo:latest")]
                ),
            ),
        ),
        status=k8s_client.V1ReplicaSetStatus(replicas=1),
    )

    stripped = _strip_replica_set(rs, "intelliops-sandbox-abc", "clone-uid", "demo-app")

    # Must not raise — this is the assertion
    serialized = ApiClient().sanitize_for_serialization(stripped)

    owner_refs = serialized["metadata"]["ownerReferences"]
    assert len(owner_refs) == 1
    ref = owner_refs[0]
    assert ref["kind"] == "Deployment"
    assert ref["name"] == "demo-app"
    assert ref["uid"] == "clone-uid"
    assert ref["controller"] is True
