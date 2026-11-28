//! Advisory text for the top hypothesis. It never changes confidence, order or
//! the suggested runbook, and any model failure falls back to the template.

use intelliops_contracts::{EnrichmentContext, RootCauseHypothesis, Situation};
use intelliops_llm::ChatClient;
use serde_json::{Map, Value, json};

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

/// Only deploys of services in this incident. Handed every deploy, the model
/// blamed whichever one it saw, even for an unrelated service.
fn own_deploys<'a>(
    context: &'a EnrichmentContext,
    situation: &Situation,
) -> Vec<&'a Map<String, Value>> {
    context
        .recent_deploys
        .iter()
        .filter(|deploy| {
            deploy
                .get("service")
                .and_then(Value::as_str)
                .is_some_and(|name| {
                    situation
                        .member_events
                        .iter()
                        .any(|e| e.labels.get("service").is_some_and(|s| s == name))
                })
        })
        .collect()
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
                 Evidence: {}. Suggested runbook: {}. Recent deploys of the affected services: {}.",
                situation.id,
                situation.severity,
                hypothesis.description,
                hypothesis.confidence,
                json!(hypothesis.evidence),
                hypothesis.suggested_runbook_id.as_deref().unwrap_or("none"),
                json!(own_deploys(context, situation)),
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

#[cfg(test)]
#[allow(clippy::expect_used)]
mod tests {
    use super::*;

    #[test]
    fn deploys_of_other_services_are_left_out() {
        let situation: Situation = serde_json::from_value(json!({
            "id": "sit-1", "status": "detected", "severity": "low", "signature": "sig",
            "first_seen": 0, "last_seen": 1,
            "member_events": [{"source": "s", "kind": "metric", "name": "cpu", "value": 1.0, "fingerprint": "fp",
                               "labels": {"service": "demo-app"}, "ts": 0}],
        }))
        .expect("situation");
        let deploy = |service: &str, version: &str| {
            Map::from_iter([
                ("service".to_string(), json!(service)),
                ("version".to_string(), json!(version)),
            ])
        };
        let context = EnrichmentContext {
            recent_deploys: vec![deploy("meridian-gateway", "v2"), deploy("demo-app", "v7")],
            ..EnrichmentContext::default()
        };
        let kept = own_deploys(&context, &situation);
        assert_eq!(kept.len(), 1);
        assert_eq!(kept[0]["version"], "v7");
    }
}
