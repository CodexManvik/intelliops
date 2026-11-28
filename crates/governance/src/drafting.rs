//! Drafting in the background, with its steps streamed live and stored.

use std::collections::{HashMap, HashSet};
use std::convert::Infallible;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{StatusCode, header};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use futures_util::stream::{self, Stream};
use intelliops_contracts::{Timestamp, TraceStep, TraceStepKind};
use intelliops_store::Store;
use serde::Deserialize;
use serde_json::json;
use subtle::ConstantTimeEq;
use tokio::sync::broadcast::{self, error::RecvError};
use uuid::Uuid;

use crate::api::{ApiError, Governance, ProposeRequest};
use crate::author::{Trace, TraceSink};

const KEEPALIVE: Duration = Duration::from_secs(15);
const BACKLOG: usize = 1000;

#[derive(Default)]
struct Runs {
    live: HashMap<String, broadcast::Sender<TraceStep>>,
    ended: HashSet<String>,
}

/// Live fan-out of each run's steps. The store is the durable record.
#[derive(Clone, Default)]
pub struct Hub(Arc<Mutex<Runs>>);

impl Hub {
    fn lock(&self) -> MutexGuard<'_, Runs> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn sender(&self, run_id: &str) -> broadcast::Sender<TraceStep> {
        self.lock()
            .live
            .entry(run_id.to_string())
            .or_insert_with(|| broadcast::channel(BACKLOG).0)
            .clone()
    }

    /// `None` once the run has ended.
    fn subscribe(&self, run_id: &str) -> Option<broadcast::Receiver<TraceStep>> {
        if self.lock().ended.contains(run_id) {
            return None;
        }
        Some(self.sender(run_id).subscribe())
    }

    fn publish(&self, step: &TraceStep) {
        // Fails only when nobody is watching.
        let _ = self.sender(&step.run_id).send(step.clone());
    }

    fn end(&self, run_id: &str) {
        let mut runs = self.lock();
        runs.live.remove(run_id);
        runs.ended.insert(run_id.to_string());
    }
}

struct Recorder {
    hub: Hub,
    store: Store,
}

impl TraceSink for Recorder {
    async fn record(&mut self, step: TraceStep) {
        self.hub.publish(&step);
        if let Err(error) = self.store.run_step(&step).await {
            tracing::warn!(%error, "could not store a trace step");
        }
    }
}

async fn run(g: Governance, run_id: String, request: ProposeRequest) {
    let recorder = Recorder {
        hub: g.hub.clone(),
        store: g.store.clone(),
    };
    let mut trace = Trace::new(&run_id, recorder);
    let draft = match &g.author {
        Some(author) => {
            let toolbox = g.toolbox(request.situation.clone());
            author
                .draft(&toolbox, request.hint.as_deref(), Some(&mut trace))
                .await
        }
        None => None,
    };
    let (status, proposal_id) = match draft {
        Some(draft) => match g
            .finalize(&request.situation, draft, &request.requested_by)
            .await
        {
            Ok(proposal) => ("succeeded", Some(proposal.id)),
            Err(error) => {
                tracing::error!(%error, "could not store the proposal");
                ("failed", None)
            }
        },
        None => ("gave_up", None),
    };
    trace.outcome(status, proposal_id.as_deref()).await;
    if let Err(error) = g
        .store
        .run_finish(&run_id, status, proposal_id.as_deref(), Timestamp::now())
        .await
    {
        tracing::warn!(%error, "could not finish the run record");
    }
    g.hub.end(&run_id);
}

pub(crate) async fn draft_in_background(
    State(g): State<Governance>,
    Json(request): Json<ProposeRequest>,
) -> Result<Response, ApiError> {
    g.may_propose(&request.requested_by)?;
    let mut run_id = format!("run-{}", Uuid::new_v4().simple());
    run_id.truncate(12);
    if let Err(error) = g
        .store
        .run_start(&run_id, &request.situation.signature, Timestamp::now())
        .await
    {
        tracing::warn!(%error, "could not start the run record");
    }
    // Create the channel before anyone can subscribe.
    g.hub.sender(&run_id);
    tokio::spawn(run(g, run_id.clone(), request));
    Ok((StatusCode::ACCEPTED, Json(json!({"run_id": run_id}))).into_response())
}

struct Follow {
    pending: std::collections::VecDeque<TraceStep>,
    live: Option<broadcast::Receiver<TraceStep>>,
    sent_through: i64,
    connected: bool,
}

fn data(step: &TraceStep) -> Event {
    Event::default().data(serde_json::to_string(step).unwrap_or_default())
}

/// Stored steps first, then live ones, until the outcome.
fn follow(state: Follow) -> impl Stream<Item = Result<Event, Infallible>> {
    stream::unfold(state, |mut state| async move {
        if !state.connected {
            state.connected = true;
            return Some((Ok(Event::default().comment("connected")), state));
        }
        if let Some(step) = state.pending.pop_front() {
            state.sent_through = state.sent_through.max(step.seq);
            return Some((Ok(data(&step)), state));
        }
        loop {
            match state.live.as_mut()?.recv().await {
                // Already replayed from the store.
                Ok(step) if step.seq <= state.sent_through => {}
                Ok(step) => {
                    state.sent_through = step.seq;
                    if step.kind == TraceStepKind::Outcome {
                        state.live = None;
                    }
                    return Some((Ok(data(&step)), state));
                }
                Err(RecvError::Lagged(_)) => {}
                Err(RecvError::Closed) => return None,
            }
        }
    })
}

#[derive(Deserialize)]
pub(crate) struct StreamQuery {
    token: Option<String>,
}

pub(crate) async fn stream_run(
    State(g): State<Governance>,
    Path(run_id): Path<String>,
    Query(query): Query<StreamQuery>,
) -> Response {
    if let Some(expected) = &g.stream_token {
        let given = query.token.unwrap_or_default();
        if !bool::from(given.as_bytes().ct_eq(expected.as_bytes())) {
            return ApiError(StatusCode::UNAUTHORIZED, "Unauthorized".to_string()).into_response();
        }
    }
    // Subscribe before reading the store, or a step published in between is in
    // neither.
    let live = g.hub.subscribe(&run_id);
    let stored = g.store.run_steps(&run_id).await.unwrap_or_else(|error| {
        tracing::warn!(%error, "could not read stored steps");
        Vec::new()
    });
    let events = follow(Follow {
        pending: stored.into(),
        live,
        sent_through: -1,
        connected: false,
    });
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
