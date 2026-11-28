//! The configured online detector.

use std::collections::BTreeMap;

use chrono::Timelike;
use intelliops_contracts::TelemetryEvent;
use intelliops_detect::{Detector, Robust};

#[derive(Debug, Clone)]
pub enum Scorer {
    /// Online mean and variance per metric.
    River(Detector),
    /// Seasonal median and MAD.
    Robust(Robust),
}

impl Scorer {
    pub fn score(&mut self, event: &TelemetryEvent) -> f64 {
        match self {
            Self::River(detector) => detector.detect(&event.name, event.value),
            Self::Robust(detector) => {
                let label = |key: &str| event.labels.get(key).filter(|v| !v.is_empty());
                let series = label("service")
                    .or_else(|| label("job"))
                    .map_or("", String::as_str);
                detector.detect(&event.name, series, event.ts.0.hour(), event.value)
            }
        }
    }

    /// `metric -> (centre, spread)`: what "normal" currently looks like.
    #[must_use]
    pub fn baselines(&self, metrics: &[&str]) -> BTreeMap<String, (f64, f64)> {
        match self {
            Self::River(detector) => metrics
                .iter()
                .filter_map(|name| Some(((*name).to_string(), detector.baseline(name)?)))
                .collect(),
            Self::Robust(detector) => {
                let mut all = detector.baselines();
                all.retain(|name, _| metrics.contains(&name.as_str()));
                all
            }
        }
    }

    pub fn reset(&mut self) {
        match self {
            Self::River(detector) => detector.reset(),
            Self::Robust(detector) => detector.reset(),
        }
    }
}
