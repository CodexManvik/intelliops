//! Closes the loop: every remediation outcome becomes labelled evidence, and
//! the evidence decides which playbooks may run without a human.

use std::collections::BTreeMap;

use intelliops_contracts::{
    RemediationOutcome, RemediationResult, TrainingRecord, signature_from_situation_id,
};
use serde_json::{Value, json};

const NOTE: &str = "MTTR/MTTD require end-to-end detection→resolution timestamps not yet \
                    threaded; reported metrics are outcome-derived.";

/// `worked` is true only for a clean success.
#[must_use]
pub fn label(outcome: &RemediationOutcome) -> TrainingRecord {
    TrainingRecord {
        situation_id: outcome.situation_id.clone(),
        signature: signature_from_situation_id(&outcome.situation_id).to_string(),
        playbook_id: outcome.playbook_id.clone(),
        result: outcome.result,
        worked: outcome.result == RemediationResult::Success,
        ts: outcome.ts,
        mode: Some(outcome.mode.clone()),
    }
}

/// An escalation attempted nothing, and an interrupted attempt decided
/// nothing: neither is evidence about a runbook.
#[must_use]
pub fn is_evidence(outcome: &RemediationOutcome) -> bool {
    outcome.result != RemediationResult::Escalated
        && !outcome.health_after.starts_with("interrupted:")
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct PlaybookStats {
    pub successes: u32,
    pub failures: u32,
    pub rollbacks: u32,
}

impl PlaybookStats {
    /// A simulated success proves the gates ran, not that the fix works.
    #[must_use]
    pub fn of(records: &[TrainingRecord], playbook_id: &str, count_simulated: bool) -> Self {
        let mut stats = Self::default();
        for record in records.iter().filter(|r| r.playbook_id == playbook_id) {
            match record.result {
                RemediationResult::Success => {
                    if count_simulated || record.mode.as_deref() != Some("dry_run") {
                        stats.successes += 1;
                    }
                }
                RemediationResult::Failure => stats.failures += 1,
                RemediationResult::RolledBack => stats.rollbacks += 1,
                RemediationResult::Escalated => {}
            }
        }
        stats
    }

    /// Enough successes and not one failure or rollback.
    #[must_use]
    pub fn graduates(self, min_successes: u32) -> bool {
        self.successes >= min_successes && self.failures == 0 && self.rollbacks == 0
    }
}

fn result_name(result: RemediationResult) -> &'static str {
    match result {
        RemediationResult::Success => "success",
        RemediationResult::Failure => "failure",
        RemediationResult::RolledBack => "rolled_back",
        RemediationResult::Escalated => "escalated",
    }
}

/// Only what outcomes support. All rates share the attempted-only denominator.
#[must_use]
pub fn metrics(records: &[TrainingRecord]) -> Value {
    let mut by_result: BTreeMap<&str, u32> = ["success", "failure", "rolled_back", "escalated"]
        .map(|name| (name, 0))
        .into();
    let mut by_signature: BTreeMap<&str, (u32, u32)> = BTreeMap::new();
    for record in records {
        *by_result.entry(result_name(record.result)).or_default() += 1;
        let entry = by_signature.entry(&record.signature).or_default();
        entry.0 += u32::from(record.worked);
        entry.1 += 1;
    }
    let count = |name: &str| by_result.get(name).copied().unwrap_or(0);
    let attempted = records.len() - count("escalated") as usize;
    let rate = |name: &str| {
        if attempted == 0 {
            0.0
        } else {
            f64::from(count(name)) / attempted as f64
        }
    };
    let signatures: BTreeMap<&str, Value> = by_signature
        .into_iter()
        .map(|(sig, (worked, total))| (sig, json!({"worked": worked, "total": total})))
        .collect();
    json!({
        "total_outcomes": records.len(),
        "success_rate": rate("success"),
        "rollback_rate": rate("rolled_back"),
        "failure_rate": rate("failure"),
        "by_result": by_result,
        "by_signature": signatures,
        "note": NOTE,
    })
}
