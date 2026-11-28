//! The same fault clusters and idle curves as the Python workloads.

#![allow(clippy::unwrap_used)]

use axum::body::{Body, to_bytes};
use axum::http::Request;
use intelliops_service::Config;
use intelliops_sim::apps::{Sim, demo_app, meridian};
use intelliops_sim::jitter::jitter;
use intelliops_sim::state::{Fault, State};
use serde_json::Value;
use tower::ServiceExt;

fn recording() -> Value {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../app-cases/sim.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn faults_move_exactly_the_recorded_metrics() {
    for case in recording()["faults"].as_array().unwrap() {
        let at = format!("{} x{}", case["type"], case["magnitude"]);
        let mut state = State::default();
        state.apply(&Fault {
            kind: case["type"].as_str().unwrap().to_string(),
            magnitude: case["magnitude"].as_f64().unwrap(),
            duration_seconds: Some(100.0),
        });
        for step in case["ramp"].as_array().unwrap() {
            let mut sampled = state.clone();
            sampled.sample_at(step[0].as_f64().unwrap());
            let expected = step[1].as_f64().unwrap();
            assert!((sampled.memory_usage_mb - expected).abs() < 0.01, "{at}");
        }
        let ours = [
            ("cpu", state.cpu),
            ("error_rate", state.error_rate),
            ("latency_ms", state.latency_ms),
            ("request_rate", state.request_rate),
            ("latency_p50_ms", state.latency_p50_ms),
            ("latency_p99_ms", state.latency_p99_ms),
            ("memory_usage_mb", state.memory_usage_mb),
            ("saturation", state.saturation),
            ("queue_depth", state.queue_depth),
            ("db_pool_in_use", state.db_pool_in_use),
            ("db_pool_max", state.db_pool_max),
            ("disk_usage_percent", state.disk_usage_percent),
            ("service_up", state.service_up),
            ("tls_handshake_failures", state.tls_handshake_failures),
        ];
        for (name, value) in ours {
            assert_eq!(case["state"][name].as_f64(), Some(value), "{at}: {name}");
        }
    }
}

#[test]
fn jitter_follows_the_same_curve() {
    for case in recording()["jitter"].as_array().unwrap() {
        let number = |i: usize| case[i].as_f64().unwrap();
        let ours = jitter(
            case[0].as_str().unwrap(),
            case[1].as_str().unwrap(),
            number(2),
            number(3),
        );
        assert!((ours - number(4)).abs() < 1e-9, "{case}: {ours}");
    }
}

async fn get(app: &axum::Router, method: &str, path: &str) -> (u16, String) {
    let request = Request::builder()
        .method(method)
        .uri(path)
        .body(Body::empty())
        .unwrap();
    let response = app.clone().oneshot(request).await.unwrap();
    let status = response.status().as_u16();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, String::from_utf8(body.to_vec()).unwrap())
}

#[tokio::test]
async fn breaking_the_demo_app_shows_in_its_metrics() {
    let app = demo_app(Sim::new("demo-app", Config::from_vars([])));
    assert_eq!(get(&app, "GET", "/work").await.0, 200);
    assert_eq!(get(&app, "POST", "/break").await.0, 200);
    assert_eq!(get(&app, "GET", "/work").await.0, 500);
    let (_, metrics) = get(&app, "GET", "/metrics").await;
    assert!(metrics.contains("\ncpu_usage 92\n"), "{metrics}");
    assert!(metrics.contains("\nhttp_request_errors_total 1\n"));
    assert!(!metrics.contains("service_up"));
}

#[tokio::test]
async fn controls_need_the_token_but_metrics_do_not() {
    let config = Config::from_vars([
        ("INTELLIOPS_AUTH_MODE".to_string(), "token".to_string()),
        ("INTELLIOPS_AUTH_TOKEN".to_string(), "t".to_string()),
    ]);
    let app = meridian(Sim::new("meridian-validation", config), None);
    assert_eq!(get(&app, "POST", "/admin/clear").await.0, 401);
    assert_eq!(get(&app, "GET", "/metrics").await.0, 200);
    assert_eq!(get(&app, "GET", "/api/ops/metrics").await.0, 404);
}
