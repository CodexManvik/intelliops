//! Deterministic rules decide which runbooks are candidates. Embeddings and
//! the track record only adjust how confident we are in that vetted choice.

use intelliops_contracts::{
    DiagnosedSituation, EnrichmentContext, Playbook, RootCauseHypothesis, Situation,
    SituationStatus, TelemetryKind,
};
use serde_json::Value;

/// How much a proven track record can add: enough to reorder close rules,
/// never to invert a large gap.
const RELIABILITY_WEIGHT: f64 = 0.15;

/// Worked/total of a runbook on a signature: `(signature, runbook)`.
pub type TrackRecord<'a> = &'a dyn Fn(&str, &str) -> f64;

/// Semantic fit between an incident and a playbook's written symptoms.
pub trait Selector {
    fn score(
        &self,
        situation: &Situation,
        hypothesis: &RootCauseHypothesis,
        playbook: &Playbook,
    ) -> Option<f64>;

    /// The best playbook of the catalog, if any fits well enough.
    fn select(
        &self,
        situation: &Situation,
        hypothesis: &RootCauseHypothesis,
        playbooks: &[Playbook],
    ) -> Option<(String, f64)>;
}

fn text(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        // As Python formats a missing value.
        None => "None".to_string(),
    }
}

fn hypothesis(
    situation: &Situation,
    description: String,
    confidence: f64,
    evidence: Vec<String>,
    runbook: Option<&str>,
) -> RootCauseHypothesis {
    RootCauseHypothesis {
        situation_id: situation.id.clone(),
        description,
        confidence,
        evidence,
        suggested_runbook_id: runbook.map(str::to_string),
        explanation: None,
        explanation_source: None,
        confidence_source: None,
    }
}

/// The rules, in a fixed order that breaks confidence ties.
fn propose(situation: &Situation, context: &EnrichmentContext) -> Vec<RootCauseHypothesis> {
    let mut found = Vec::new();
    let services: Vec<&String> = situation
        .member_events
        .iter()
        .filter_map(|e| e.labels.get("service").filter(|s| !s.is_empty()))
        .collect();

    let deploy = context.recent_deploys.iter().find(|deploy| {
        deploy
            .get("service")
            .and_then(Value::as_str)
            .is_some_and(|name| services.iter().any(|s| *s == name))
    });
    if let Some(deploy) = deploy {
        let service = text(deploy.get("service"));
        let version = text(deploy.get("version"));
        found.push(hypothesis(
            situation,
            format!("recent deployment of {service} ({version}) preceded the incident"),
            0.8,
            vec![format!("deploy {service}@{version}")],
            Some("rollback-deploy"),
        ));
    }

    let names = situation
        .member_events
        .iter()
        .map(|e| e.name.to_lowercase())
        .collect::<Vec<_>>()
        .join(" ");
    let mentions = |tokens: &[&str]| tokens.iter().any(|t| names.contains(t));
    let mut by_metrics = |description: &str, confidence, runbook| {
        found.push(hypothesis(
            situation,
            description.to_string(),
            confidence,
            vec![format!("metrics: {names}")],
            Some(runbook),
        ));
    };

    // A process that is not serving is recycled, not scaled.
    if mentions(&["service_up"]) {
        by_metrics(
            "service is down — the process stopped serving",
            0.7,
            "restart-pod",
        );
    }
    // New replicas of a leaking service leak too, so restart beats scaling.
    if mentions(&["memory"]) {
        by_metrics(
            "memory pressure / leak — recycle the process",
            0.65,
            "restart-pod",
        );
    }
    if mentions(&["cpu", "disk", "saturation"]) {
        by_metrics(
            "resource saturation across the affected service",
            0.6,
            "scale-service",
        );
    }
    // "duration" is OpenTelemetry's word for latency.
    if mentions(&["latency", "duration", "queue_depth", "request_rate"]) {
        by_metrics(
            "latency/queueing under load — capacity contention",
            0.55,
            "scale-service",
        );
    }
    // Scaling would only exhaust the same pool again.
    if mentions(&["db_pool"]) {
        by_metrics(
            "database connection-pool exhaustion — recycle connections",
            0.62,
            "restart-pod",
        );
    }
    let errors = situation
        .member_events
        .iter()
        .any(|e| e.kind == TelemetryKind::Log || e.name.to_lowercase().contains("error"));
    if errors {
        found.push(hypothesis(
            situation,
            "error spike in service logs".to_string(),
            0.58,
            vec!["log/error events present".to_string()],
            Some("restart-pod"),
        ));
    }
    if found.is_empty() {
        found.push(hypothesis(
            situation,
            "root cause undetermined from available signals".to_string(),
            0.2,
            Vec::new(),
            None,
        ));
    }
    found
}

pub fn rank(
    situation: &Situation,
    context: &EnrichmentContext,
    playbooks: &[Playbook],
    selector: Option<&dyn Selector>,
    reliability: Option<TrackRecord<'_>>,
) -> Vec<RootCauseHypothesis> {
    let mut hypotheses = propose(situation, context);
    for h in &mut hypotheses {
        let Some(id) = &h.suggested_runbook_id else {
            continue;
        };
        let score = selector.and_then(|selector| {
            let playbook = playbooks.iter().find(|p| p.id == *id)?;
            selector.score(situation, h, playbook)
        });
        h.confidence_source = Some(match score {
            Some(score) => {
                h.confidence = score.clamp(0.0, 1.0);
                "embedding".to_string()
            }
            None => "rule".to_string(),
        });
    }
    let boosted = |h: &RootCauseHypothesis| {
        let record = match (&reliability, &h.suggested_runbook_id) {
            (Some(reliability), Some(id)) if !situation.signature.is_empty() => {
                reliability(&situation.signature, id).clamp(0.0, 1.0)
            }
            _ => 0.0,
        };
        (h.confidence + RELIABILITY_WEIGHT * record).min(1.0)
    };
    // Stable, best first. Confidence breaks ties between clamped scores.
    hypotheses.sort_by(|a, b| {
        let primary = if reliability.is_some() {
            boosted(b).total_cmp(&boosted(a))
        } else {
            std::cmp::Ordering::Equal
        };
        primary.then_with(|| b.confidence.total_cmp(&a.confidence))
    });
    hypotheses
}

/// Rank, then pick the runbook: the top rule's if it is registered, otherwise
/// the best semantic match among registered playbooks.
pub fn diagnose(
    situation: Situation,
    context: &EnrichmentContext,
    playbooks: &[Playbook],
    selector: Option<&dyn Selector>,
    reliability: Option<TrackRecord<'_>>,
) -> DiagnosedSituation {
    let mut hypotheses = rank(&situation, context, playbooks, selector, reliability);
    let registered = |id: &str| playbooks.iter().any(|p| p.id == id);
    let mut runbook = hypotheses
        .first()
        .and_then(|h| h.suggested_runbook_id.clone())
        .filter(|id| registered(id));
    if runbook.is_none()
        && let (Some(selector), Some(top)) = (selector, hypotheses.first_mut())
        && let Some((id, score)) = selector.select(&situation, top, playbooks)
        && registered(&id)
    {
        top.suggested_runbook_id = Some(id.clone());
        top.evidence
            .push(format!("semantic match: {id} ({score:.2})"));
        runbook = Some(id);
    }
    let suggested_runbook_id = runbook.or_else(|| {
        hypotheses
            .first()
            .and_then(|h| h.suggested_runbook_id.clone())
    });
    DiagnosedSituation {
        situation: Situation {
            status: SituationStatus::Diagnosed,
            ..situation
        },
        hypotheses,
        suggested_runbook_id,
    }
}
