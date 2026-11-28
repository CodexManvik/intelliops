use std::collections::HashSet;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use intelliops_bus::Consumer;
use intelliops_contracts::{HitlMode, RemediationOutcome, RemediationResult, topics};
use intelliops_feedback::{PlaybookStats, is_evidence, label, metrics};
use intelliops_service::{
    Config, Failure, Service, app, bus, consumer_config, port, serve, start, store,
};
use intelliops_store::Store;
use serde_json::json;

const GOVERNANCE_TIMEOUT: Duration = Duration::from_secs(5);
const STORE_BACKOFF: Duration = Duration::from_secs(2);
const ACTOR: &str = "feedback-service";

/// Graduate and demote calls. Best effort: governance's audit is the record,
/// and the next matching outcome tries again.
struct Governance {
    client: reqwest::Client,
    url: String,
    token: Option<String>,
}

impl Governance {
    async fn call(&self, playbook_id: &str, verb: &str) -> bool {
        let url = format!("{}/playbooks/{playbook_id}/{verb}", self.url);
        let mut request = self.client.post(url).json(&json!({"decided_by": ACTOR}));
        if let Some(token) = &self.token {
            request = request.bearer_auth(token);
        }
        match request.send().await {
            Ok(response) if response.status().is_success() => true,
            Ok(response) => {
                tracing::warn!(status = %response.status(), playbook_id, verb, "refused");
                false
            }
            Err(error) => {
                tracing::warn!(%error, playbook_id, verb, "governance unreachable");
                false
            }
        }
    }
}

struct Loop {
    store: Store,
    governance: Governance,
    min_successes: u32,
    count_simulated: bool,
}

impl Loop {
    async fn run(self, mut consumer: Consumer) {
        let mut graduated: HashSet<String> = HashSet::new();
        loop {
            let outcome: RemediationOutcome = consumer.next().await;
            if !is_evidence(&outcome) {
                continue;
            }
            // The record must land: a lost outcome silently skews the evidence.
            while let Err(error) = self.store.training_append(&label(&outcome)).await {
                tracing::error!(%error, "could not store the outcome; retrying");
                tokio::time::sleep(STORE_BACKOFF).await;
            }
            let id = &outcome.playbook_id;
            if id.is_empty() {
                continue;
            }
            let failed = matches!(
                outcome.result,
                RemediationResult::Failure | RemediationResult::RolledBack
            );
            if outcome.hitl_mode == HitlMode::Auto && failed {
                // It failed unattended: put the next run back behind a human.
                self.governance.call(id, "demote").await;
                graduated.remove(id);
                continue;
            }
            if graduated.contains(id) {
                continue;
            }
            match self.store.training_all().await {
                Ok(records) => {
                    let stats = PlaybookStats::of(&records, id, self.count_simulated);
                    if stats.graduates(self.min_successes)
                        && self.governance.call(id, "graduate").await
                    {
                        graduated.insert(id.clone());
                    }
                }
                Err(error) => tracing::warn!(%error, "could not read the track record"),
            }
        }
    }
}

async fn report(State(store): State<Store>) -> Response {
    match store.training_all().await {
        Ok(records) => Json(metrics(&records)).into_response(),
        Err(error) => {
            tracing::error!(%error, "could not read the track record");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"detail": "database unavailable"})),
            )
                .into_response()
        }
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    start("feedback", run).await
}

fn flag(config: &Config, name: &str) -> bool {
    matches!(
        config.get(name, "false").to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
    let store = store(&config).await?;
    let token = config.get("auth_token", "");
    let closing = Loop {
        store: store.clone(),
        governance: Governance {
            client: reqwest::Client::builder()
                .timeout(GOVERNANCE_TIMEOUT)
                .build()?,
            url: config.get("governance_url", "http://governance:8000"),
            token: (config.get("auth_mode", "off") == "token" && !token.is_empty())
                .then_some(token),
        },
        min_successes: config.number("graduation_min_successes", 3)?,
        count_simulated: flag(&config, "graduation_count_simulated"),
    };
    let outcomes = consumer_config(&config, topics::REMEDIATION_OUTCOMES, "feedback")?;
    tokio::spawn(closing.run(bus.consumer(outcomes)?));

    let routes = Router::new()
        .route("/metrics", get(report))
        .with_state(store.clone());
    let service = Service::new("feedback-service", config, bus).with_database(store);
    serve(app(Arc::new(service), routes), port()).await?;
    Ok(())
}
