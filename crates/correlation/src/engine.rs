//! Anomalies accumulate in a window keyed on event time; the window collapses
//! into one situation when its span is exceeded or it is flushed. A signature
//! with a proven fix record is suppressed: dropped, or marked to be handled
//! quietly.

use std::collections::{HashMap, VecDeque};

use indexmap::IndexMap;
use intelliops_contracts::{
    Handling, Situation, SituationStatus, TelemetryEvent, signature, situation_id,
};
use intelliops_detect::Policy;
use serde_json::{Map, Value, json};

use crate::Scorer;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Grouping {
    /// One bucket: concurrent faults merge into one situation.
    #[default]
    Window,
    /// One bucket per `service` label.
    Service,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SuppressionMode {
    /// A suppressed situation is never emitted, so never fixed.
    #[default]
    Drop,
    /// It is emitted marked quiet, to be fixed without paging anyone.
    Quiet,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EngineConfig {
    pub z_threshold: f64,
    pub policy: Policy,
    pub window_seconds: f64,
    pub grouping: Grouping,
    /// Anomalies a window needs to be an incident.
    pub min_events: usize,
    pub suppress_threshold: f64,
    /// Labelled outcomes a signature needs before it can be suppressed.
    pub suppress_min_samples: u32,
    pub suppress_mode: SuppressionMode,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            z_threshold: 3.0,
            policy: Policy::Off,
            window_seconds: 30.0,
            grouping: Grouping::Window,
            min_events: 1,
            suppress_threshold: 0.8,
            suppress_min_samples: 1,
            suppress_mode: SuppressionMode::Drop,
        }
    }
}

/// One labelled remediation outcome.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Training {
    pub signature: String,
    pub worked: bool,
}

#[derive(Debug, Clone, Default)]
struct Bucket {
    events: Vec<TelemetryEvent>,
    peak: f64,
}

impl Bucket {
    fn span_to(&self, last: &TelemetryEvent) -> f64 {
        let Some(first) = self.events.first() else {
            return 0.0;
        };
        let span = last.ts.0.signed_duration_since(first.ts.0);
        span.num_microseconds()
            .map_or(f64::MAX, |micros| micros as f64 / 1e6)
    }
}

fn severity(peak: f64) -> &'static str {
    if peak >= 8.0 {
        "high"
    } else if peak >= 5.0 {
        "medium"
    } else {
        "low"
    }
}

#[derive(Debug, Clone)]
pub struct Engine {
    config: EngineConfig,
    scorer: Scorer,
    /// Insertion order is the order `flush_all` emits in.
    buckets: IndexMap<String, Bucket>,
    suppressed: VecDeque<Situation>,
    /// signature -> (worked, total)
    record: HashMap<String, (u32, u32)>,
}

impl Engine {
    #[must_use]
    pub fn new(mut config: EngineConfig, scorer: Scorer) -> Self {
        config.min_events = config.min_events.max(1);
        config.suppress_min_samples = config.suppress_min_samples.max(1);
        Self {
            config,
            scorer,
            buckets: IndexMap::new(),
            suppressed: VecDeque::new(),
            record: HashMap::new(),
        }
    }

    #[must_use]
    pub fn scorer(&self) -> &Scorer {
        &self.scorer
    }

    pub fn scorer_mut(&mut self) -> &mut Scorer {
        &mut self.scorer
    }

    /// Score one event. Returns the situation its window closed, if any.
    pub fn add(&mut self, event: TelemetryEvent) -> Option<Situation> {
        let score = self.scorer.score(&event);
        if !self
            .config
            .policy
            .is_anomaly(&event.name, event.value, score, self.config.z_threshold)
        {
            return None;
        }
        let key = match self.config.grouping {
            Grouping::Window => String::new(),
            Grouping::Service => event
                .labels
                .get("service")
                .filter(|s| !s.is_empty())
                .map_or_else(|| "unknown".to_string(), Clone::clone),
        };
        let overflowed = self.buckets.get(&key).is_some_and(|bucket| {
            !bucket.events.is_empty() && bucket.span_to(&event) > self.config.window_seconds
        });
        let emitted = if overflowed { self.close(&key) } else { None };
        let bucket = self.buckets.entry(key).or_default();
        bucket.events.push(event);
        bucket.peak = bucket.peak.max(score);
        emitted
    }

    /// Close every window. Dropped situations are omitted.
    pub fn flush_all(&mut self) -> Vec<Situation> {
        let keys: Vec<String> = self.buckets.keys().cloned().collect();
        keys.iter().filter_map(|key| self.close(key)).collect()
    }

    fn close(&mut self, key: &str) -> Option<Situation> {
        let bucket = self.buckets.get(key)?;
        let last = bucket.events.last()?;
        if bucket.events.len() < self.config.min_events {
            // A young window has not had its full chance yet. An aged one that
            // is still thin was never an incident: drop it.
            if bucket.span_to(last) >= self.config.window_seconds {
                self.buckets.shift_remove(key);
            }
            return None;
        }
        let bucket = self.buckets.shift_remove(key)?;
        let mut situation = self.situation(bucket)?;
        if self.should_suppress(&situation.signature) {
            match self.config.suppress_mode {
                SuppressionMode::Quiet => {
                    situation.handling = Handling::Quiet;
                    self.suppressed.push_back(situation.clone());
                }
                SuppressionMode::Drop => {
                    self.suppressed.push_back(situation);
                    return None;
                }
            }
        }
        Some(situation)
    }

    fn situation(&self, bucket: Bucket) -> Option<Situation> {
        let Bucket { events, peak } = bucket;
        let first_seen = events.iter().map(|e| e.ts).min()?;
        let last_seen = events.iter().map(|e| e.ts).max()?;
        let signature = signature(events.iter().map(|e| e.fingerprint.as_str()));
        let metrics: Vec<&str> = events.iter().map(|e| e.name.as_str()).collect();
        let baseline: Map<String, Value> = self
            .scorer
            .baselines(&metrics)
            .into_iter()
            .map(|(name, (mean, std))| (name, json!({"mean": mean, "std": std})))
            .collect();
        Some(Situation {
            id: situation_id(&signature),
            status: SituationStatus::Detected,
            severity: severity(peak).to_string(),
            first_seen,
            last_seen,
            signature,
            peak_score: Some(peak),
            baseline: Some(baseline),
            handling: Handling::Normal,
            member_events: events,
        })
    }

    fn should_suppress(&self, signature: &str) -> bool {
        self.record.get(signature).is_some_and(|&(worked, total)| {
            total >= self.config.suppress_min_samples
                && f64::from(worked) / f64::from(total) >= self.config.suppress_threshold
        })
    }

    /// Replace the track record with one computed from `outcomes`.
    pub fn retrain(&mut self, outcomes: &[Training]) {
        self.record.clear();
        for outcome in outcomes {
            let entry = self.record.entry(outcome.signature.clone()).or_default();
            entry.0 += u32::from(outcome.worked);
            entry.1 += 1;
        }
    }

    /// The oldest suppressed situation not yet published.
    pub fn pop_suppressed(&mut self) -> Option<Situation> {
        self.suppressed.pop_front()
    }

    /// Forget what "normal" looks like, but not which fixes have worked.
    pub fn reset(&mut self) {
        self.scorer.reset();
        self.buckets.clear();
        self.suppressed.clear();
    }
}
