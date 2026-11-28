//! The consumer, the timer that closes windows, and the operator routes.

use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use intelliops_bus::{Bus, Consumer};
use intelliops_contracts::{Handling, Situation, TelemetryEvent, topics};
use intelliops_detect::BaselineRow;
use intelliops_service::publish;
use intelliops_store::Store;
use serde_json::{Value, json};
use tokio::sync::Mutex;
use tokio::time::Instant;

use crate::{Engine, Scorer, Training};

#[derive(Clone)]
pub struct Correlation {
    pub engine: Arc<Mutex<Engine>>,
    pub store: Store,
    pub bus: Bus,
    pub kind: String,
}

impl Correlation {
    /// Publish what an operation emitted, then whatever it suppressed.
    async fn emit(&self, emitted: Vec<Situation>) {
        for situation in &emitted {
            publish(&self.bus, topics::SITUATIONS_DETECTED, situation).await;
        }
        loop {
            let next = self.engine.lock().await.pop_suppressed();
            let Some(situation) = next else { return };
            tracing::info!(
                situation_id = %situation.id,
                signature = %situation.signature,
                quiet = situation.handling == Handling::Quiet,
                "suppressed situation"
            );
            publish(&self.bus, topics::SITUATIONS_SUPPRESSED, &situation).await;
        }
    }

    /// Re-read the labelled outcomes. A failed read keeps the current map.
    pub async fn refresh_reliability(&self) {
        match self.store.training_all().await {
            Ok(records) => {
                let outcomes: Vec<Training> = records
                    .into_iter()
                    .filter(|r| r.is_real())
                    .map(|r| Training {
                        signature: r.signature,
                        worked: r.worked,
                    })
                    .collect();
                self.engine.lock().await.retrain(&outcomes);
            }
            Err(error) => tracing::warn!(%error, "reliability refresh failed"),
        }
    }

    /// Only the mean/variance detector has a baseline that fits the table.
    pub async fn restore_baseline(&self) {
        let rows = match self.store.baseline_load().await {
            Ok(rows) => rows,
            Err(error) => {
                tracing::warn!(%error, "baseline reload failed; starting cold");
                return;
            }
        };
        if let Scorer::River(detector) = self.engine.lock().await.scorer_mut() {
            let rows: Vec<BaselineRow> = rows
                .into_iter()
                .map(|r| BaselineRow {
                    metric_name: r.metric_name,
                    n: r.n,
                    mean: r.mean,
                    variance: r.variance,
                    count: u64::try_from(r.count).unwrap_or(0),
                })
                .collect();
            detector.load(&rows);
        }
    }

    async fn save_baseline(&self) {
        let rows: Vec<intelliops_store::BaselineRow> = match self.engine.lock().await.scorer() {
            Scorer::River(detector) => detector
                .snapshot()
                .into_iter()
                .map(|r| intelliops_store::BaselineRow {
                    metric_name: r.metric_name,
                    n: r.n,
                    mean: r.mean,
                    variance: r.variance,
                    count: i64::try_from(r.count).unwrap_or(i64::MAX),
                })
                .collect(),
            Scorer::Robust(_) => return,
        };
        if let Err(error) = self.store.baseline_save(&rows).await {
            tracing::warn!(%error, "baseline snapshot failed");
        }
    }

    pub async fn consume(self, mut consumer: Consumer) {
        loop {
            let event: TelemetryEvent = consumer.next().await;
            let emitted = self.engine.lock().await.add(event);
            self.emit(emitted.into_iter().collect()).await;
        }
    }

    /// Close windows on elapsed time: once the baseline absorbs a fault, no
    /// later event would trigger it. Also snapshots and refreshes.
    pub async fn flush_periodically(
        self,
        period: Duration,
        snapshot_every: Duration,
        refresh_every: Duration,
    ) {
        let mut snapshot = Instant::now();
        let mut refresh = Instant::now();
        loop {
            tokio::time::sleep(period).await;
            self.flush().await;
            if snapshot.elapsed() >= snapshot_every {
                self.save_baseline().await;
                snapshot = Instant::now();
            }
            if refresh.elapsed() >= refresh_every {
                self.refresh_reliability().await;
                refresh = Instant::now();
            }
        }
    }

    pub async fn flush(&self) {
        let emitted = self.engine.lock().await.flush_all();
        self.emit(emitted).await;
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route("/reset-baseline", post(reset_baseline))
            .route("/retrain", post(retrain))
            .route("/baseline", get(baseline))
            .with_state(self)
    }
}

async fn reset_baseline(State(correlation): State<Correlation>) -> Response {
    correlation.engine.lock().await.reset();
    if let Err(error) = correlation.store.baseline_clear().await {
        tracing::error!(%error, "could not clear the stored baseline");
        return (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail": "database unavailable"})),
        )
            .into_response();
    }
    Json(json!({"reset": true})).into_response()
}

/// Neither online detector has a model to fit.
async fn retrain() -> Json<Value> {
    Json(json!({"fitted": false, "persisted": false}))
}

async fn baseline(State(correlation): State<Correlation>) -> Json<Value> {
    let engine = correlation.engine.lock().await;
    let (statistic, rows): (&str, Vec<Value>) = match engine.scorer() {
        Scorer::River(detector) => (
            "mean/stddev",
            detector
                .snapshot()
                .into_iter()
                .map(|r| {
                    json!({"metric_name": r.metric_name, "mean": r.mean,
                           "std": r.variance.powf(0.5), "count": r.count})
                })
                .collect(),
        ),
        Scorer::Robust(detector) => {
            let counts = detector.counts();
            (
                "median/MAD",
                detector
                    .baselines()
                    .into_iter()
                    .map(|(name, (mean, std))| {
                        let count = counts.get(&name);
                        json!({"metric_name": name, "mean": mean, "std": std, "count": count})
                    })
                    .collect(),
            )
        }
    };
    Json(json!({
        "correlator_kind": correlation.kind,
        "statistic": statistic,
        "baselines": rows,
    }))
}
