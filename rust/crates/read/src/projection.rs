//! Events folded into the exact shapes `frontend/src/data/types.ts` expects.
//! It holds no durable state: the streams are the source of truth.

use std::collections::BTreeMap;

use indexmap::IndexMap;
use intelliops_contracts::{
    DiagnosedSituation, Handling, HitlMode, RemediationOutcome, RemediationResult, Situation,
    Timestamp,
};
use serde::Serialize;
use serde_json::{Map, Value, json};

const MAX_MEMBER_EVENTS: usize = 20;

/// Truncated like Python's `int(dt.timestamp() * 1000)`.
fn epoch_ms(ts: Timestamp) -> i64 {
    ((ts.0.timestamp_micros() as f64 / 1e6) * 1000.0) as i64
}

#[derive(Debug, Clone, Serialize)]
pub struct Card {
    id: String,
    signature: String,
    service: String,
    title: String,
    status: String,
    severity: &'static str,
    #[serde(rename = "memberCount")]
    member_count: usize,
    member_events: Vec<Value>,
    peak_score: Option<f64>,
    baseline: Option<Map<String, Value>>,
    first_seen: i64,
    last_seen: i64,
    hypotheses: Vec<Value>,
    suggested_runbook_id: Option<String>,
    hitl_mode: &'static str,
    reversible: bool,
    reliability: f64,
    suppressed: bool,
    handling: Handling,
    last_activity: i64,
    stages: BTreeMap<String, i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    outcome: Option<Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct OutcomeRow {
    situation_id: String,
    playbook_id: String,
    result: RemediationResult,
    reason: String,
    ts: i64,
    service: String,
    hitl_mode: HitlMode,
    mode: String,
    handling: Handling,
    mttr_ms: Option<i64>,
}

/// Terminal: aged out after the TTL.
fn is_terminal(status: &str) -> bool {
    matches!(status, "resolved" | "failed")
}

/// May be dropped to stay under the cap. `needs_attention` is never aged out,
/// so the incident that most needs a human does not vanish on a timer.
fn is_evictable(status: &str) -> bool {
    is_terminal(status) || status == "needs_attention"
}

fn result_status(result: RemediationResult) -> &'static str {
    match result {
        RemediationResult::Success => "resolved",
        RemediationResult::Failure | RemediationResult::RolledBack => "failed",
        RemediationResult::Escalated => "needs_attention",
    }
}

fn severity(value: &str) -> &'static str {
    match value {
        "critical" => "critical",
        "high" => "high",
        "low" => "low",
        _ => "medium",
    }
}

fn service_of(situation: &Situation) -> String {
    situation
        .member_events
        .iter()
        .find_map(|event| {
            ["service", "job", "instance"]
                .iter()
                .find_map(|key| event.labels.get(*key).filter(|v| !v.is_empty()))
        })
        .map_or_else(|| "unknown".to_string(), Clone::clone)
}

/// One decimal, ties to even on the exact binary value, as Python's `round`.
fn round(value: f64, decimals: usize) -> f64 {
    format!("{value:.decimals$}").parse().unwrap_or(value)
}

#[derive(Debug, Clone)]
pub struct ReadModel {
    max_outcomes: usize,
    ttl_ms: f64,
    max_situations: usize,
    cards: IndexMap<String, Card>,
    /// Newest first.
    outcomes: Vec<OutcomeRow>,
    suppressed: u64,
}

impl ReadModel {
    #[must_use]
    pub fn new(max_outcomes: usize, ttl_seconds: f64, max_situations: usize) -> Self {
        Self {
            max_outcomes,
            ttl_ms: ttl_seconds * 1000.0,
            max_situations,
            cards: IndexMap::new(),
            outcomes: Vec::new(),
            suppressed: 0,
        }
    }

    /// Fold in a detection. `false` if it was stale. An id is a signature, so a
    /// recurrence arrives under the same id and `first_seen` tells them apart:
    /// older is late, newer replaces a finished card, equal never moves the
    /// status backwards.
    pub fn apply_detected(&mut self, situation: &Situation) -> bool {
        let first = epoch_ms(situation.first_seen);
        let mut held = self.cards.get(&situation.id);
        if let Some(card) = held {
            if first < card.first_seen {
                return false;
            }
            if first > card.first_seen && is_evictable(&card.status) {
                held = None;
            }
        }
        let mut status = "detected".to_string();
        if let Ok(Value::String(name)) = serde_json::to_value(situation.status) {
            status = name;
        }
        if let Some(card) = held
            && card.first_seen == first
            && card.status != "detected"
        {
            status.clone_from(&card.status);
        }
        let member_events = situation
            .member_events
            .iter()
            .take(MAX_MEMBER_EVENTS)
            .map(|e| {
                json!({"name": e.name, "value": e.value, "labels": e.labels,
                       "kind": e.kind, "ts": epoch_ms(e.ts)})
            })
            .collect();
        let mut stages = held.map(|c| c.stages.clone()).unwrap_or_default();
        stages.entry("detected".to_string()).or_insert(first);
        let card = Card {
            id: situation.id.clone(),
            signature: situation.signature.clone(),
            service: service_of(situation),
            title: situation.signature.clone(),
            status,
            severity: severity(&situation.severity),
            member_count: situation.member_events.len(),
            member_events,
            peak_score: situation.peak_score,
            baseline: situation.baseline.clone(),
            first_seen: first,
            last_seen: epoch_ms(situation.last_seen),
            hypotheses: held.map(|c| c.hypotheses.clone()).unwrap_or_default(),
            suggested_runbook_id: held.and_then(|c| c.suggested_runbook_id.clone()),
            hitl_mode: held.map_or("hitl", |c| c.hitl_mode),
            reversible: held.is_none_or(|c| c.reversible),
            reliability: held.map_or(0.0, |c| c.reliability),
            suppressed: false,
            handling: situation.handling,
            last_activity: held.map_or(first, |c| c.last_activity),
            stages,
            outcome: held.and_then(|c| c.outcome.clone()),
        };
        self.cards.insert(situation.id.clone(), card);
        true
    }

    pub fn apply_suppressed(&mut self) {
        self.suppressed += 1;
    }

    pub fn apply_diagnosed(&mut self, diagnosed: &DiagnosedSituation) -> bool {
        if !self.apply_detected(&diagnosed.situation) {
            return false;
        }
        let Some(card) = self.cards.get_mut(&diagnosed.situation.id) else {
            return false; // INFALLIBLE: apply_detected inserted it
        };
        card.hypotheses = diagnosed
            .hypotheses
            .iter()
            .map(|h| {
                json!({
                    "description": h.description,
                    "confidence": h.confidence,
                    "suggested_runbook_id": h.suggested_runbook_id,
                    "evidence": h.evidence,
                    "explanation": h.explanation,
                    "explanation_source": h.explanation_source,
                    "confidence_source": h.confidence_source,
                })
            })
            .collect();
        card.title = diagnosed.hypotheses.first().map_or_else(
            || diagnosed.situation.signature.clone(),
            |h| format!("{} · {}", h.description, card.service),
        );
        card.suggested_runbook_id
            .clone_from(&diagnosed.suggested_runbook_id);
        card.stages.insert(
            "diagnosed".to_string(),
            epoch_ms(diagnosed.situation.last_seen),
        );
        // A diagnosis that arrives after its outcome must not reopen the card.
        if card.status == "detected" {
            card.status = "diagnosed".to_string();
        }
        true
    }

    pub fn apply_outcome(&mut self, outcome: &RemediationOutcome) {
        let at = epoch_ms(outcome.ts);
        if let Some(card) = self.cards.get_mut(&outcome.situation_id) {
            let status = result_status(outcome.result);
            card.status = status.to_string();
            card.last_activity = at;
            card.stages.insert(status.to_string(), at);
            card.outcome = Some(json!({
                "result": outcome.result,
                "health_after": outcome.health_after,
                "mode": outcome.mode,
                "steps": outcome.steps,
                "preflight": outcome.preflight,
                "handling": outcome.handling,
            }));
        }
        let card = self.cards.get(&outcome.situation_id);
        // A recurrence overwrites `first_seen`, so a replayed old outcome can
        // precede it: no measurement rather than a negative one.
        let mttr_ms = card
            .filter(|_| outcome.result == RemediationResult::Success)
            .map(|c| at - c.first_seen)
            .filter(|elapsed| *elapsed >= 0);
        let row = OutcomeRow {
            situation_id: outcome.situation_id.clone(),
            playbook_id: outcome.playbook_id.clone(),
            result: outcome.result,
            reason: outcome.health_after.clone(),
            ts: at,
            service: card.map_or_else(|| "unknown".to_string(), |c| c.service.clone()),
            hitl_mode: outcome.hitl_mode,
            mode: outcome.mode.clone(),
            handling: outcome.handling,
            mttr_ms,
        };
        self.outcomes.insert(0, row);
        self.outcomes.truncate(self.max_outcomes);
    }

    fn age_out(&mut self, now_ms: i64) {
        let ttl = self.ttl_ms;
        self.cards.retain(|_, card| {
            !(is_terminal(&card.status) && (now_ms - card.last_activity) as f64 > ttl)
        });
    }

    /// Over the cap, drop the oldest finished cards, never an active one.
    fn enforce_cap(&mut self) {
        if self.cards.len() <= self.max_situations {
            return;
        }
        let mut evictable: Vec<(bool, i64, String)> = self
            .cards
            .values()
            .filter(|c| is_evictable(&c.status))
            .map(|c| (c.status == "needs_attention", c.last_activity, c.id.clone()))
            .collect();
        // Stable, so equal keys keep insertion order.
        evictable.sort_by_key(|(attention, activity, _)| (*attention, *activity));
        let excess = self.cards.len() - self.max_situations;
        for (_, _, id) in evictable.into_iter().take(excess) {
            self.cards.shift_remove(&id);
        }
    }

    pub fn situations(&mut self, now_ms: Option<i64>) -> Vec<Card> {
        if let Some(now) = now_ms {
            self.age_out(now);
        }
        self.enforce_cap();
        self.cards.values().cloned().collect()
    }

    #[must_use]
    pub fn outcomes(&self) -> &[OutcomeRow] {
        &self.outcomes
    }

    #[must_use]
    pub fn situation(&self, id: &str) -> Option<&Card> {
        self.cards.get(id)
    }

    pub fn reset(&mut self) {
        self.cards.clear();
        self.outcomes.clear();
        self.suppressed = 0;
    }

    pub fn metrics(&mut self) -> Value {
        // The same cap as `situations`, so both endpoints count one set.
        self.enforce_cap();
        let cards: Vec<&Card> = self.cards.values().collect();
        let attempted: Vec<&OutcomeRow> = self
            .outcomes
            .iter()
            .filter(|o| o.result != RemediationResult::Escalated)
            .collect();
        let total = attempted.len();
        let successes = attempted
            .iter()
            .filter(|o| o.result == RemediationResult::Success)
            .count();
        let autos = attempted
            .iter()
            .filter(|o| o.hitl_mode == HitlMode::Auto)
            .count();
        let repairs: Vec<i64> = self.outcomes.iter().filter_map(|o| o.mttr_ms).collect();
        let alerts: usize = cards.iter().map(|c| c.member_count).sum();
        let open: Vec<&&Card> = cards
            .iter()
            .filter(|c| {
                matches!(
                    c.status.as_str(),
                    "detected" | "diagnosed" | "acting" | "needs_attention"
                )
            })
            .collect();
        let pending = open
            .iter()
            .filter(|c| {
                c.hitl_mode == "hitl" && matches!(c.status.as_str(), "diagnosed" | "acting")
            })
            .count();
        let noise = if alerts == 0 {
            0.0
        } else {
            (1.0 - cards.len() as f64 / alerts as f64) * 100.0
        };
        let mttr = if repairs.is_empty() {
            0.0
        } else {
            round(
                repairs.iter().sum::<i64>() as f64 / repairs.len() as f64 / 60_000.0,
                2,
            )
        };
        let share = |part: usize, decimals: usize, scale: f64| {
            if total == 0 {
                0.0
            } else {
                round(part as f64 / total as f64 * scale, decimals)
            }
        };
        json!({
            "alertsIngested": alerts,
            "situationsOpen": open.len(),
            "noiseReductionPct": round(noise.max(0.0), 1),
            "mttrMinutes": mttr,
            "autoRemediatedPct": share(autos, 1, 100.0),
            "suppressedToday": self.suppressed,
            "approvalsPending": pending,
            "successRate": share(successes, 3, 1.0),
            "needsAttention": cards.iter().filter(|c| c.status == "needs_attention").count(),
            "quietlyHandled":
                self.outcomes.iter().filter(|o| o.handling == Handling::Quiet).count(),
        })
    }
}
