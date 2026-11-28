//! The explanation model can be configured live from the console. The routes
//! carry a key, so token auth mode gates them like everything else.

use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use intelliops_contracts::{
    EnrichmentContext, RootCauseHypothesis, Situation, SituationStatus, Timestamp,
};
use intelliops_llm::{ChatClient, redact};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::sync::RwLock;

use crate::explain::explain;

#[derive(Default)]
pub struct Explainer {
    /// `None` means the offline template.
    pub chat: Option<ChatClient>,
    pub last_probe: Option<Value>,
}

#[derive(Clone)]
pub struct Llm {
    pub explainer: Arc<RwLock<Explainer>>,
    pub default_model: String,
}

fn default_model() -> String {
    "gpt-4o-mini".to_string()
}

fn default_timeout() -> f64 {
    10.0
}

#[derive(Deserialize)]
struct LlmConfig {
    #[serde(default)]
    endpoint: String,
    #[serde(default)]
    api_key: String,
    #[serde(default = "default_model")]
    model: String,
    #[serde(default = "default_timeout")]
    timeout_seconds: f64,
}

/// A client that makes one attempt: an explanation is not worth waiting for.
pub fn chat_client(
    endpoint: &str,
    model: &str,
    api_key: &str,
    timeout_seconds: f64,
) -> Option<ChatClient> {
    if endpoint.is_empty() {
        return None;
    }
    let timeout = Duration::try_from_secs_f64(timeout_seconds).ok()?;
    ChatClient::new(endpoint, model, api_key, timeout)
        .map(|client| client.with_attempts(1))
        .map_err(|error| tracing::warn!(%error, "cannot build the model client"))
        .ok()
}

impl Llm {
    pub fn routes(self) -> Router {
        Router::new()
            .route("/config/llm", get(show).post(update))
            .route("/config/llm/test", post(probe))
            .with_state(self)
    }

    /// Never the key, and only the host of the endpoint.
    async fn state(&self) -> Value {
        let explainer = self.explainer.read().await;
        let chat = explainer.chat.as_ref();
        json!({
            "provider": if chat.is_some() { "openai-compatible" } else { "template" },
            "endpoint_configured": chat.is_some(),
            "endpoint": redact(chat.map_or("", ChatClient::base_url)),
            "model": chat.map_or(&self.default_model, |c| &c.model),
            "last_probe": explainer.last_probe,
        })
    }
}

async fn show(State(llm): State<Llm>) -> Json<Value> {
    Json(llm.state().await)
}

async fn update(State(llm): State<Llm>, Json(config): Json<LlmConfig>) -> Json<Value> {
    llm.explainer.write().await.chat = chat_client(
        &config.endpoint,
        &config.model,
        &config.api_key,
        config.timeout_seconds,
    );
    Json(llm.state().await)
}

async fn probe(State(llm): State<Llm>, Json(config): Json<LlmConfig>) -> Json<Value> {
    if config.endpoint.is_empty() {
        return Json(json!({"ok": false, "error": "no endpoint configured"}));
    }
    let chat = chat_client(
        &config.endpoint,
        &config.model,
        &config.api_key,
        config.timeout_seconds,
    );
    let hypothesis = RootCauseHypothesis {
        situation_id: "probe".into(),
        description: "probe".into(),
        confidence: 0.5,
        evidence: vec!["probe".into()],
        suggested_runbook_id: None,
        explanation: None,
        explanation_source: None,
        confidence_source: None,
    };
    let now = Timestamp::now();
    let situation = Situation {
        id: "probe".into(),
        status: SituationStatus::Detected,
        member_events: Vec::new(),
        severity: "low".into(),
        first_seen: now,
        last_seen: now,
        signature: "probe".into(),
        peak_score: None,
        baseline: None,
        handling: intelliops_contracts::Handling::Normal,
    };
    let started = Instant::now();
    let (_, source) = explain(
        chat.as_ref(),
        &hypothesis,
        &EnrichmentContext::default(),
        &situation,
    )
    .await;
    let ok = source == "llm";
    let mut probe = json!({
        "ok": ok,
        "model": config.model,
        "latency_ms": u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX),
    });
    if !ok {
        probe["error"] =
            "endpoint unreachable or returned no usable content (fell back to template)".into();
    }
    llm.explainer.write().await.last_probe = Some(probe.clone());
    Json(probe)
}
