//! A decision that lands at the deadline is honoured, not reported as a
//! timeout after governance recorded an approval.

#![allow(clippy::unwrap_used)]

use std::time::Duration;

use axum::routing::{get, post};
use axum::{Json, Router};
use intelliops_action::Gate;
use intelliops_action::governance::HttpGate;
use serde_json::{Value, json};

fn approval(status: &str) -> Json<Value> {
    Json(
        json!({"id": "appr-1", "situation_id": "s", "playbook_id": "p",
        "requested_by": "action-service", "status": status}),
    )
}

/// Every poll says pending; closing the request reveals `at_close`.
async fn outcome(at_close: &'static str) -> String {
    let app = Router::new()
        .route("/approvals/appr-1", get(|| async { approval("pending") }))
        .route(
            "/approvals/appr-1/expire",
            post(move || async move { approval(at_close) }),
        );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await });
    let wait = Duration::from_millis(100);
    let gate = HttpGate::new(&url, None, wait, Duration::from_millis(50)).unwrap();
    gate.await_decision("appr-1").await
}

#[tokio::test]
async fn the_database_settles_a_race_at_the_deadline() {
    assert_eq!(outcome("approved").await, "approved");
    assert_eq!(outcome("rejected").await, "rejected");
    assert_eq!(outcome("expired").await, "pending");
}
