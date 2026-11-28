//! The console's API, including the live event stream.

use std::convert::Infallible;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::extract::{Path, Query, State};
use axum::http::{StatusCode, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::stream::{self, Stream};
use serde::Deserialize;
use serde_json::json;
use subtle::ConstantTimeEq;
use tokio::sync::broadcast::{self, error::RecvError};

use crate::ReadModel;
use crate::peers::Peers;

const KEEPALIVE: Duration = Duration::from_secs(15);
const SUBSCRIBER_BACKLOG: usize = 1000;
const CHANGED: &str = r#"{"type": "changed"}"#;

#[derive(Clone)]
pub struct Read {
    model: Arc<Mutex<ReadModel>>,
    changes: broadcast::Sender<()>,
    peers: Arc<Peers>,
    /// Set in token auth mode.
    token: Option<String>,
}

impl Read {
    #[must_use]
    pub fn new(model: ReadModel, peers: Peers, token: Option<String>) -> Self {
        Self {
            model: Arc::new(Mutex::new(model)),
            changes: broadcast::channel(SUBSCRIBER_BACKLOG).0,
            peers: Arc::new(peers),
            token,
        }
    }

    pub fn model(&self) -> MutexGuard<'_, ReadModel> {
        self.model.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Tell every open stream that something changed.
    pub fn notify(&self) {
        // Fails only when nobody is listening.
        let _ = self.changes.send(());
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route("/situations", get(situations))
            .route("/situations/{id}", get(situation))
            .route("/outcomes", get(outcomes))
            .route("/metrics", get(metrics))
            .route("/metrics/history", get(history))
            .route("/system", get(system))
            .route("/reset", post(reset))
            .route("/stream", get(stream_changes))
            .with_state(self)
    }
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

fn detail(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"detail": message}))).into_response()
}

async fn situations(State(read): State<Read>) -> Response {
    let cards = read.model().situations(Some(now_ms()));
    Json(cards).into_response()
}

async fn situation(State(read): State<Read>, Path(id): Path<String>) -> Response {
    let card = read.model().situation(&id).cloned();
    match card {
        Some(card) => Json(card).into_response(),
        None => detail(StatusCode::NOT_FOUND, "situation not found"),
    }
}

async fn outcomes(State(read): State<Read>) -> Response {
    let rows = read.model().outcomes().to_vec();
    Json(rows).into_response()
}

async fn metrics(State(read): State<Read>) -> Response {
    let value = read.model().metrics();
    Json(value).into_response()
}

async fn reset(State(read): State<Read>) -> Response {
    read.model().reset();
    Json(json!({"reset": true})).into_response()
}

async fn system(State(read): State<Read>) -> Response {
    Json(read.peers.system().await).into_response()
}

#[derive(Deserialize)]
struct HistoryQuery {
    metric: Option<String>,
    minutes: Option<f64>,
    step_seconds: Option<f64>,
    service: Option<String>,
}

async fn history(State(read): State<Read>, Query(query): Query<HistoryQuery>) -> Response {
    let metric = query.metric.unwrap_or_else(|| "cpu_usage".to_string());
    let service = query.service;
    if !safe_metric(&metric) {
        return detail(StatusCode::BAD_REQUEST, "invalid metric name");
    }
    if service.as_deref().is_some_and(|s| !safe_label(s)) {
        return detail(StatusCode::BAD_REQUEST, "invalid service name");
    }
    let body = read
        .peers
        .history(
            &metric,
            query.minutes.unwrap_or(15.0),
            query.step_seconds.unwrap_or(15.0),
            service.as_deref(),
        )
        .await;
    Json(body).into_response()
}

/// Both values land inside a PromQL selector, so they are allowlisted.
fn safe_metric(name: &str) -> bool {
    let mut chars = name.chars();
    name.len() <= 81
        && chars
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

fn safe_label(value: &str) -> bool {
    (1..=80).contains(&value.len())
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "_.:-".contains(c))
}

#[derive(Deserialize)]
struct StreamQuery {
    token: Option<String>,
}

/// `EventSource` cannot set headers, so this route takes `?token=` instead.
async fn stream_changes(State(read): State<Read>, Query(query): Query<StreamQuery>) -> Response {
    if let Some(expected) = &read.token {
        let given = query.token.unwrap_or_default();
        if !bool::from(given.as_bytes().ct_eq(expected.as_bytes())) {
            return detail(StatusCode::UNAUTHORIZED, "Unauthorized");
        }
    }
    let events = events(read.changes.subscribe());
    let sse = Sse::new(events).keep_alive(KeepAlive::new().interval(KEEPALIVE).text("keepalive"));
    (
        [
            (header::CACHE_CONTROL, "no-cache"),
            (header::HeaderName::from_static("x-accel-buffering"), "no"),
        ],
        sse,
    )
        .into_response()
}

fn events(receiver: broadcast::Receiver<()>) -> impl Stream<Item = Result<Event, Infallible>> {
    stream::unfold((receiver, true), |(mut receiver, first)| async move {
        if first {
            return Some((Ok(Event::default().comment("connected")), (receiver, false)));
        }
        match receiver.recv().await {
            // A lagging reader missed some "changed" notes; one more says it all.
            Ok(()) | Err(RecvError::Lagged(_)) => {
                Some((Ok(Event::default().data(CHANGED)), (receiver, false)))
            }
            Err(RecvError::Closed) => None,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selector_inputs_are_allowlisted() {
        assert!(safe_metric("cpu_usage"));
        assert!(!safe_metric("1x"));
        assert!(!safe_metric("a{b=\"c\"}"));
        assert!(safe_label("meridian-gateway:8000"));
        assert!(!safe_label("x\"}"));
        assert!(!safe_label(""));
    }
}
