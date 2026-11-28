//! Against a real API server. Runs only when `INTELLIOPS_KUBE_TEST=1`, on the
//! current kube context: point it at a disposable cluster.
//!
//!     kind create cluster --name intelliops-rs-test
//!     INTELLIOPS_KUBE_TEST=1 cargo test -p intelliops-action --test live_cluster

#![allow(clippy::unwrap_used)]

use std::time::Duration;

use intelliops_action::kubernetes::{KubeHealth, KubeRemediator};
use intelliops_action::sandbox::CloneSandbox;
use intelliops_action::{Health, Rehearsal, Remediator};
use intelliops_contracts::{RemediationPlan, RemediationTarget, Situation};
use k8s_openapi::api::apps::v1::Deployment;
use k8s_openapi::api::core::v1::Namespace;
use kube::api::{Api, DeleteParams, PostParams};
use kube::{Client, ResourceExt};
use serde_json::{Value, json};

const NAMESPACE: &str = "intelliops-live-test";

fn plan(steps: Value, rollback: Value) -> RemediationPlan {
    serde_json::from_value(json!({
        "target": {"namespace": NAMESPACE, "deployment": "gateway"},
        "steps": steps,
        "rollback_steps": rollback,
    }))
    .unwrap()
}

fn situation() -> Situation {
    serde_json::from_value(json!({
        "id": "sit-live", "status": "diagnosed", "severity": "low", "signature": "live",
        "first_seen": "2026-01-01T00:00:00Z", "last_seen": "2026-01-01T00:00:00Z",
    }))
    .unwrap()
}

#[tokio::test]
async fn remediates_a_real_deployment() {
    if std::env::var("INTELLIOPS_KUBE_TEST").as_deref() != Ok("1") {
        eprintln!("skipped: INTELLIOPS_KUBE_TEST is not set");
        return;
    }
    let client = Client::try_default().await.unwrap();
    let namespaces: Api<Namespace> = Api::all(client.clone());
    let namespace = serde_json::from_value(json!({
        "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE},
    }))
    .unwrap();
    namespaces
        .create(&PostParams::default(), &namespace)
        .await
        .unwrap();
    let deployments: Api<Deployment> = Api::namespaced(client.clone(), NAMESPACE);
    let deployment = serde_json::from_value(json!({
        "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "gateway"},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "gateway"}},
            "template": {"metadata": {"labels": {"app": "gateway"}},
                "spec": {"terminationGracePeriodSeconds": 1,
                    "containers": [{"name": "main", "image": "registry.k8s.io/pause:3.10"}]}}},
    }))
    .unwrap();
    deployments
        .create(&PostParams::default(), &deployment)
        .await
        .unwrap();

    let health = KubeHealth {
        client: client.clone(),
        metrics: None,
        timeout: Duration::from_secs(120),
        poll: Duration::from_secs(1),
    };
    let target = RemediationTarget {
        namespace: NAMESPACE.into(),
        deployment: "gateway".into(),
    };
    let situation = situation();
    assert!(
        health.check(&situation, &target).await,
        "never became ready"
    );

    let read = || async { deployments.get("gateway").await.unwrap() };
    let replicas = |d: &Deployment| d.spec.as_ref().unwrap().replicas.unwrap();
    let annotated = |d: &Deployment| {
        d.spec
            .as_ref()
            .unwrap()
            .template
            .metadata
            .as_ref()
            .unwrap()
            .annotations
            .clone()
            .unwrap_or_default()
    };
    let mut remediator = KubeRemediator::new(client.clone());

    let restart = plan(json!([{"action": "restart"}]), json!([]));
    assert!(remediator.execute(&restart).await, "restart");
    assert!(annotated(&read().await).contains_key("kubectl.kubernetes.io/restartedAt"));
    assert!(health.check(&situation, &target).await, "after restart");

    let scale = plan(
        json!([{"action": "scale", "replicas": 2}]),
        json!([{"action": "scale", "replicas": -2}]),
    );
    assert!(remediator.execute(&scale).await, "scale");
    assert_eq!(replicas(&read().await), 3);
    assert!(health.check(&situation, &target).await, "after scale");
    assert!(remediator.rollback(&scale).await, "scale rollback");
    assert_eq!(replicas(&read().await), 1);

    let limits = plan(
        json!([{"action": "patch_resource_limits", "mem_limit": "64Mi", "cpu_limit": "100m"}]),
        json!([]),
    );
    assert!(remediator.execute(&limits).await, "limits");
    let resources = |d: Deployment| {
        d.spec.unwrap().template.spec.unwrap().containers[0]
            .resources
            .clone()
            .and_then(|r| r.limits)
    };
    assert!(resources(read().await).unwrap().contains_key("memory"));
    assert!(health.check(&situation, &target).await, "after limits");

    let probe = plan(
        json!([{"action": "patch_probe", "probe": "readiness", "period_seconds": 7}]),
        json!([]),
    );
    // A probe patch with no handler is refused by the API server: a failed
    // step, not a crash.
    assert!(!remediator.execute(&probe).await, "probe without a handler");

    let back = plan(
        json!([{"action": "rollback_to_revision", "revision": 1}]),
        json!([]),
    );
    assert!(remediator.execute(&back).await, "rollback to revision 1");
    assert!(resources(read().await).is_none(), "limits should be gone");
    assert!(health.check(&situation, &target).await, "after rollback");

    let sandbox = CloneSandbox {
        client: client.clone(),
        namespace: NAMESPACE.into(),
    };
    let rehearsal = sandbox.rehearse(&situation, &restart).await;
    assert!(rehearsal.passed, "{}", rehearsal.detail);
    let sandbox_namespace = rehearsal.sandbox_namespace.unwrap();
    let left = namespaces.get_opt(&sandbox_namespace).await.unwrap();
    assert!(
        left.is_none_or(|n| n.metadata.deletion_timestamp.is_some()),
        "sandbox {sandbox_namespace} was not torn down"
    );
    assert_eq!(read().await.name_any(), "gateway");

    namespaces
        .delete(NAMESPACE, &DeleteParams::default())
        .await
        .unwrap();
}
