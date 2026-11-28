//! The control guarantees, through the HTTP API on the in-memory store.

#![allow(clippy::unwrap_used)]

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::http::Request;
use axum::routing::post;
use intelliops_governance::author::Author;
use intelliops_governance::context::SystemContext;
use intelliops_governance::rbac::{Policy, fnmatch};
use intelliops_governance::{Governance, Hub};
use intelliops_llm::ChatClient;
use intelliops_store::Store;
use serde_json::{Value, json};
use tower::ServiceExt;

fn repo(path: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join(path)
}

fn governance(author: Option<Author>) -> Governance {
    Governance {
        store: Store::memory(),
        policy: Arc::new(Policy::load(&repo("policies/rbac_policy.yaml")).unwrap()),
        author: author.map(Arc::new),
        context: SystemContext(repo("config/system_context.yaml")),
        hub: Hub::default(),
        stream_token: None,
    }
}

async fn call(app: &Router, method: &str, path: &str, body: Value) -> (u16, Value) {
    let request = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json")
        .body(if body.is_null() {
            Body::empty()
        } else {
            Body::from(body.to_string())
        })
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let bytes = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

#[test]
fn globs_match_python_fnmatch() {
    let raw = std::fs::read_to_string(repo("rust/app-cases/fnmatch.json")).unwrap();
    let cases: Vec<(String, String, bool)> = serde_json::from_str(&raw).unwrap();
    assert!(cases.len() > 2000);
    let wrong: Vec<_> = cases
        .iter()
        .filter(|(name, pattern, expected)| fnmatch(name, pattern) != *expected)
        .take(10)
        .collect();
    assert!(wrong.is_empty(), "{wrong:?}");
}

#[tokio::test]
async fn an_approval_is_decided_once_by_someone_allowed_and_audited() {
    let app = governance(None).routes();
    let request = json!({"id": "appr-1", "situation_id": "sit-1",
        "playbook_id": "restart-pod", "requested_by": "action-service"});
    assert_eq!(call(&app, "POST", "/approvals", request).await.0, 200);
    let decide = |decision: &str, by: &str| json!({"decision": decision, "decided_by": by});
    let path = "/approvals/appr-1/decide";
    assert_eq!(
        call(&app, "POST", path, decide("yes", "oncall-alice"))
            .await
            .0,
        422
    );
    assert_eq!(
        call(&app, "POST", path, decide("approved", "mallory"))
            .await
            .0,
        403
    );
    let (status, body) = call(&app, "POST", path, decide("approved", "oncall-alice")).await;
    assert_eq!((status, body["status"].as_str()), (200, Some("approved")));
    let (status, body) = call(&app, "POST", path, decide("rejected", "oncall-alice")).await;
    assert_eq!(status, 409);
    assert_eq!(body["detail"], "approval is already approved");
    let (_, audit) = call(&app, "GET", "/audit?correlation_id=sit-1", Value::Null).await;
    assert_eq!(audit.as_array().unwrap().len(), 1);
    assert_eq!(audit[0]["actor"], "oncall-alice");
}

#[tokio::test]
async fn the_registry_cannot_be_written_without_permission() {
    let app = governance(None).routes();
    let playbook = json!({"id": "x", "name": "x", "match_rule": "x", "hitl_mode": "auto"});
    let path = "/playbooks?registered_by=oncall-alice";
    assert_eq!(call(&app, "POST", path, playbook.clone()).await.0, 403);
    let path = "/playbooks?registered_by=action-service";
    assert_eq!(call(&app, "POST", path, playbook).await.0, 403);
    let by = json!({"decided_by": "oncall-alice"});
    assert_eq!(
        call(&app, "POST", "/playbooks/x/graduate", by.clone())
            .await
            .0,
        404
    );
    let path = "/playbooks/restart-pod/graduate";
    // Seeding is a boot step, so this in-memory registry is empty.
    assert_eq!(call(&app, "POST", path, by).await.0, 404);
}

/// A scripted model: investigates, submits an unsafe action, then a safe one.
async fn model(calls: Arc<std::sync::atomic::AtomicUsize>) -> String {
    use std::sync::atomic::Ordering;
    let reply = move || {
        let calls = calls.clone();
        async move {
            let tool = |name: &str, arguments: Value| {
                axum::Json(json!({"choices": [{"message": {"content": "thinking",
                    "tool_calls": [{"id": "c", "type": "function",
                        "function": {"name": name, "arguments": arguments.to_string()}}]}}]}))
            };
            let draft = |action: &str| {
                json!({"playbook": {"name": "n", "match_rule": "m", "hitl_mode": "auto",
                    "id": "chosen-by-model", "steps": [{"action": action}]},
                    "rationale": "r", "cited_facts": ["f"]})
            };
            match calls.fetch_add(1, Ordering::SeqCst) {
                0 => tool("get_incident_details", json!({"situation_id": "other"})),
                1 => tool("submit_runbook", draft("delete_namespace")),
                _ => tool("submit_runbook", draft("restart")),
            }
        }
    };
    let app = Router::new().route("/chat/completions", post(reply));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await });
    format!("http://{address}")
}

#[tokio::test]
async fn the_author_cannot_propose_an_unlisted_action_or_choose_trust() {
    let calls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let url = model(calls.clone()).await;
    let author = Author {
        chat: ChatClient::new(&url, "m", "", Duration::from_secs(5)).unwrap(),
    };
    let app = governance(Some(author)).routes();
    let request = json!({"situation": {"id": "sit-9", "signature": "9", "severity": "low"},
        "requested_by": "oncall-alice"});
    let (status, accepted) = call(&app, "POST", "/playbooks/draft-async", request).await;
    assert_eq!(status, 202);
    let run = format!("/agent-runs/{}", accepted["run_id"].as_str().unwrap());
    let mut steps = Vec::new();
    for _ in 0..200 {
        let (_, body) = call(&app, "GET", &run, Value::Null).await;
        steps = body["steps"].as_array().unwrap().clone();
        if steps.iter().any(|s| s["kind"] == "outcome") {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert_eq!(calls.load(std::sync::atomic::Ordering::SeqCst), 3);
    assert_eq!(steps.last().unwrap()["detail"]["status"], "succeeded");
    assert!(steps.iter().any(|s| s["tool"] == "get_incident_details"));

    let (_, proposals) = call(&app, "GET", "/playbooks/proposed", Value::Null).await;
    let playbook = &proposals[0]["playbook"];
    assert_eq!(playbook["hitl_mode"], "hitl");
    assert!(playbook["id"].as_str().unwrap().starts_with("ai-9-"));
    assert_eq!(playbook["steps"][0]["action"], "restart");

    let id = proposals[0]["id"].as_str().unwrap();
    let by = json!({"decided_by": "oncall-alice"});
    let approve = format!("/playbooks/proposed/{id}/approve");
    assert_eq!(call(&app, "POST", &approve, by.clone()).await.0, 200);
    let reject = format!("/playbooks/proposed/{id}/reject");
    assert_eq!(call(&app, "POST", &reject, by).await.0, 409);
}
