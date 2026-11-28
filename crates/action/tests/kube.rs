//! The exact requests sent to the API server, against a mock one.

#![allow(clippy::unwrap_used)]

use std::pin::pin;

use http::{Request, Response};
use intelliops_action::Remediator;
use intelliops_action::kubernetes::{KubeRemediator, converged};
use intelliops_contracts::{RemediationPlan, RemediationTarget};
use kube::Client;
use kube::client::Body;
use serde_json::{Value, json};
use tower_test::mock;

type Seen = Vec<(String, String, Value)>;

fn deployment(replicas: i32) -> Value {
    json!({"apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "gateway", "namespace": "demo"},
        "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": "gateway"}},
            "template": {"spec": {"containers": [{"name": "main"}]}}}})
}

/// Run `plan` against a server that answers every request with `replicas`.
async fn drive(plan: Value, replicas: i32, rollback: bool) -> (bool, Seen) {
    let (service, handle) = mock::pair::<Request<Body>, Response<Body>>();
    let server = tokio::spawn(async move {
        let mut handle = pin!(handle);
        let mut seen = Seen::new();
        while let Some((request, send)) = handle.next_request().await {
            let method = request.method().to_string();
            let path = request.uri().path().to_string();
            let bytes = request.into_body().collect_bytes().await.unwrap();
            let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
            let object = if path.ends_with("/scale") {
                json!({"apiVersion": "autoscaling/v1", "kind": "Scale",
                    "metadata": {"name": "gateway", "namespace": "demo"},
                    "spec": {"replicas": replicas}})
            } else if path.ends_with("/replicasets") {
                json!({"apiVersion": "apps/v1", "kind": "ReplicaSetList",
                    "metadata": {}, "items": []})
            } else {
                deployment(replicas)
            };
            seen.push((method, path, body));
            let reply = Body::from(serde_json::to_vec(&object).unwrap());
            send.send_response(Response::new(reply));
        }
        seen
    });
    let mut remediator = KubeRemediator::new(Client::new(service, "demo"));
    let plan: RemediationPlan = serde_json::from_value(plan).unwrap();
    let mut ok = remediator.execute(&plan).await;
    if rollback {
        ok = remediator.rollback(&plan).await;
    }
    drop(remediator);
    (ok, server.await.unwrap())
}

fn plan(steps: Value, rollback: Value) -> Value {
    let target = RemediationTarget {
        namespace: "demo".into(),
        deployment: "gateway".into(),
    };
    json!({"target": target, "steps": steps, "rollback_steps": rollback})
}

#[tokio::test]
async fn restart_patches_one_annotation() {
    let (ok, seen) = drive(plan(json!([{"action": "restart"}]), json!([])), 1, false).await;
    assert!(ok);
    assert_eq!(seen.len(), 1);
    let (method, path, body) = &seen[0];
    assert_eq!(method, "PATCH");
    assert_eq!(path, "/apis/apps/v1/namespaces/demo/deployments/gateway");
    let annotations = &body["spec"]["template"]["metadata"]["annotations"];
    assert!(annotations["kubectl.kubernetes.io/restartedAt"].is_string());
}

#[tokio::test]
async fn scale_is_clamped_and_rollback_restores_the_original() {
    let steps = json!([{"action": "scale", "replicas": 5}]);
    let undo = json!([{"action": "scale", "replicas": -5}]);
    let (ok, seen) = drive(plan(steps, undo), 9, true).await;
    assert!(ok);
    let scales: Vec<&Value> = seen
        .iter()
        .filter(|(m, p, _)| m == "PATCH" && p.ends_with("/scale"))
        .map(|(_, _, body)| &body["spec"]["replicas"])
        .collect();
    // 9 + 5 clamps to 10; rollback goes back to 9, not 10 - 5.
    assert_eq!(scales, [&json!(10), &json!(9)]);
}

#[tokio::test]
async fn limits_go_to_the_first_container_by_name() {
    let steps = json!([{"action": "patch_resource_limits", "mem_limit": "512Mi"}]);
    let (ok, seen) = drive(plan(steps, json!([])), 1, false).await;
    assert!(ok);
    let (_, _, body) = seen.last().unwrap();
    let container = &body["spec"]["template"]["spec"]["containers"][0];
    assert_eq!(
        container,
        &json!({"name": "main", "resources": {"limits": {"memory": "512Mi"}}})
    );
}

#[tokio::test]
async fn a_missing_revision_fails_without_patching() {
    let steps = json!([{"action": "rollback_to_revision", "revision": 7}]);
    let (ok, seen) = drive(plan(steps, json!([])), 1, false).await;
    assert!(!ok);
    assert!(seen.iter().all(|(method, _, _)| method == "GET"));
}

#[test]
fn a_rollout_has_converged_only_when_every_new_pod_is_ready() {
    let status = |generation: i64, observed: i64, updated: i32, ready: i32| {
        serde_json::from_value(json!({"metadata": {"generation": generation},
            "status": {"observedGeneration": observed, "replicas": 2,
                "updatedReplicas": updated, "readyReplicas": ready}}))
        .unwrap()
    };
    assert!(converged(&status(3, 3, 2, 2)));
    assert!(!converged(&status(3, 2, 2, 2)));
    assert!(!converged(&status(3, 3, 1, 2)));
    assert!(!converged(&status(3, 3, 2, 1)));
}
