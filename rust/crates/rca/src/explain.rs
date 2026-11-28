//! Advisory text for the top hypothesis. It never changes confidence, order or
//! the suggested runbook, and any model failure falls back to the template.

use intelliops_contracts::{EnrichmentContext, RootCauseHypothesis, Situation};
use intelliops_llm::ChatClient;
use serde_json::{Value, json};

#[must_use]
pub fn template(hypothesis: &RootCauseHypothesis) -> String {
    let evidence = if hypothesis.evidence.is_empty() {
        "no direct evidence".to_string()
    } else {
        hypothesis.evidence.join("; ")
    };
    format!(
        "Likely root cause: {} (confidence {:.2}). Evidence: {evidence}. Suggested runbook: {}.",
        hypothesis.description,
        hypothesis.confidence,
        hypothesis
            .suggested_runbook_id
            .as_deref()
            .unwrap_or("no runbook available"),
    )
}

fn payload(
    model: &str,
    hypothesis: &RootCauseHypothesis,
    context: &EnrichmentContext,
    situation: &Situation,
) -> Value {
    json!({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an SRE assistant. Explain the likely root \
                cause of an incident concisely, in plain language, for an on-call engineer."},
            {"role": "user", "content": format!(
                "Situation {} (severity {}): hypothesis '{}' with confidence {:.2}. \
                 Evidence: {}. Suggested runbook: {}. Recent deploys: {}.",
                situation.id,
                situation.severity,
                hypothesis.description,
                hypothesis.confidence,
                json!(hypothesis.evidence),
                hypothesis.suggested_runbook_id.as_deref().unwrap_or("none"),
                json!(context.recent_deploys),
            )},
        ],
    })
}

/// `(text, "llm" | "template")`.
pub async fn explain(
    chat: Option<&ChatClient>,
    hypothesis: &RootCauseHypothesis,
    context: &EnrichmentContext,
    situation: &Situation,
) -> (String, &'static str) {
    if let Some(chat) = chat {
        let request = payload(&chat.model, hypothesis, context, situation);
        if let Ok(message) = chat.message(&request).await
            && let Some(content) = message["content"].as_str()
            && !content.is_empty()
        {
            return (content.to_string(), "llm");
        }
    }
    (template(hypothesis), "template")
}
