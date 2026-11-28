//! Has the firing metric recovered? Judged by the same rule that detected it.
//! Anything unknown counts as not recovered, so the caller rolls back.

use intelliops_contracts::{Situation, TelemetryKind};
use intelliops_detect::{MetricKind, Policy};

const FLAT_MIN_RELATIVE: f64 = 0.5;
const FLAT_STEP_SCORE: f64 = 6.0;

/// The metrics to re-check: metric events that carried a value.
#[must_use]
pub fn firing_metrics(situation: &Situation) -> Vec<&str> {
    situation
        .member_events
        .iter()
        .filter(|e| e.kind == TelemetryKind::Metric && e.value.is_some())
        .map(|e| e.name.as_str())
        .collect()
}

/// The z-score against the situation's baseline, if it has a usable one.
fn baseline_score(situation: &Situation, metric: &str, value: f64) -> Option<f64> {
    let entry = situation.baseline.as_ref()?.get(metric)?.as_object()?;
    let mean = entry.get("mean")?.as_f64()?;
    match entry.get("std").and_then(serde_json::Value::as_f64) {
        Some(std) if std > 0.0 => Some((value - mean) / std),
        // A metric that never moved is the strongest baseline, not a missing one.
        _ => Some(
            if (value - mean).abs() <= FLAT_MIN_RELATIVE * mean.abs().max(1.0) {
                0.0
            } else {
                FLAT_STEP_SCORE
            },
        ),
    }
}

#[must_use]
pub fn recovered(
    situation: &Situation,
    metric: &str,
    current: Option<f64>,
    policy: Policy,
    z_threshold: f64,
) -> bool {
    let Some(current) = current else { return false };
    let score = baseline_score(situation, metric, current);
    let score_only = policy == Policy::Off || MetricKind::classify(metric) == MetricKind::Default;
    if score_only {
        // Magnitude: far below normal is no more recovered than far above.
        return score.is_some_and(|z| z.abs() <= z_threshold);
    }
    !policy.is_anomaly(metric, Some(current), score.unwrap_or(0.0), z_threshold)
}
