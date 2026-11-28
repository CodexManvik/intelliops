use axum::body::Bytes;
use axum::extract::{DefaultBodyLimit, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::post;
use axum::{Json, Router};
use intelliops_bus::Bus;
use intelliops_contracts::{TelemetryEvent, Timestamp, topics};
use serde_json::{Value, json};

use crate::normalize;

const MAX_BODY_BYTES: usize = 10 * 1024 * 1024;

/// The whole batch is validated before anything is published, so a bad event
/// no longer leaves the ones before it on the bus (ADR-010).
pub fn parse_batch(body: &[u8], now: Timestamp) -> Result<Vec<TelemetryEvent>, String> {
    let body: Value = serde_json::from_slice(body).map_err(|_| "body is not JSON")?;
    let Some(Value::Array(events)) = body.get("events") else {
        return Err("events must be a list".to_string());
    };
    events
        .iter()
        .enumerate()
        .map(|(i, raw)| normalize(raw, now).map_err(|e| format!("events[{i}]: {e}")))
        .collect()
}

pub fn routes(bus: Bus) -> Router {
    Router::new()
        .route("/ingest", post(ingest))
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES))
        .with_state(bus)
}

fn failure(status: StatusCode, detail: &str) -> Response {
    (status, Json(json!({"detail": detail}))).into_response()
}

async fn ingest(State(bus): State<Bus>, body: Bytes) -> Response {
    let events = match parse_batch(&body, Timestamp::now()) {
        Ok(events) => events,
        Err(detail) => return failure(StatusCode::UNPROCESSABLE_ENTITY, &detail),
    };
    if let Err(error) = bus.publish_all(topics::TELEMETRY_RAW, &events).await {
        tracing::error!(%error, "could not publish telemetry");
        return failure(StatusCode::SERVICE_UNAVAILABLE, "event bus unavailable");
    }
    Json(json!({"accepted": events.len()})).into_response()
}
