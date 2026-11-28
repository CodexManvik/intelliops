//! The configured online detector.

use std::collections::BTreeMap;

use chrono::{Datelike, Timelike};
use intelliops_contracts::{TelemetryEvent, TelemetryKind};
use intelliops_detect::{Detector, Model, Robust, Sighting};

#[derive(Debug, Clone)]
pub enum Scorer {
    /// Online mean and variance per metric.
    River(Detector),
    /// Seasonal median and MAD.
    Robust(Robust),
    /// The robust path, plus an isolation forest once one has been fitted.
    Trained(Robust, Box<Model>),
}

fn robust(detector: &mut Robust, event: &TelemetryEvent) -> f64 {
    let label = |key: &str| event.labels.get(key).filter(|v| !v.is_empty());
    let series = label("service")
        .or_else(|| label("job"))
        .map_or("", String::as_str);
    detector.detect(&event.name, series, event.ts.0.hour(), event.value)
}

impl Scorer {
    pub fn score(&mut self, event: &TelemetryEvent) -> f64 {
        match self {
            Self::River(detector) => detector.detect(&event.name, event.value),
            Self::Robust(detector) => robust(detector, event),
            Self::Trained(detector, model) => {
                let online_score = robust(detector, event);
                model.observe(Sighting {
                    value: event.value,
                    online_score,
                    hour: event.ts.0.hour(),
                    weekday: event.ts.0.weekday().num_days_from_monday(),
                    kind: match event.kind {
                        TelemetryKind::Metric => 0,
                        TelemetryKind::Log => 1,
                        TelemetryKind::Trace => 2,
                    },
                    labels: event.labels.len(),
                })
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
            Self::Robust(detector) | Self::Trained(detector, _) => {
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
            Self::Trained(detector, model) => {
                detector.reset();
                model.reset();
            }
        }
    }
}
