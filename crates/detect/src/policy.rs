//! A z-score suits an unbounded signal against a stable baseline. A bounded
//! ratio must fire on an absolute level, and latency on either.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MetricKind {
    Ratio,
    Latency,
    Saturation,
    Default,
}

impl MetricKind {
    #[must_use]
    pub fn classify(metric: &str) -> Self {
        const PATTERNS: [(MetricKind, &[&str]); 3] = [
            (MetricKind::Ratio, &["error_rate", "error_ratio", "_ratio"]),
            (MetricKind::Latency, &["latency", "duration", "_ms"]),
            (
                MetricKind::Saturation,
                &[
                    "saturation",
                    "utilization",
                    "disk_usage",
                    "cpu_usage",
                    "_percent",
                ],
            ),
        ];
        let name = metric.to_lowercase();
        PATTERNS
            .iter()
            .find(|(_, tokens)| tokens.iter().any(|t| name.contains(t)))
            .map_or(Self::Default, |(kind, _)| *kind)
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Thresholds {
    pub ratio: f64,
    pub saturation_ratio: f64,
    pub saturation_percent: f64,
    pub latency_ceiling_ms: f64,
}

impl Default for Thresholds {
    fn default() -> Self {
        Self {
            ratio: 0.02,
            saturation_ratio: 0.80,
            saturation_percent: 90.0,
            latency_ceiling_ms: 500.0,
        }
    }
}

/// `Off` is the pure z-score decision.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum Policy {
    #[default]
    Off,
    On(Thresholds),
}

impl Policy {
    #[must_use]
    pub fn is_anomaly(
        &self,
        metric: &str,
        value: Option<f64>,
        score: f64,
        z_threshold: f64,
    ) -> bool {
        let Self::On(limits) = self else {
            return score > z_threshold;
        };
        let Some(value) = value else { return false };
        match MetricKind::classify(metric) {
            MetricKind::Ratio => value > limits.ratio,
            MetricKind::Saturation => {
                let name = metric.to_lowercase();
                let percent_scale = name.ends_with("_percent") || name == "cpu_usage";
                value
                    > if percent_scale {
                        limits.saturation_percent
                    } else {
                        limits.saturation_ratio
                    }
            }
            MetricKind::Latency => score > z_threshold || value > limits.latency_ceiling_ms,
            MetricKind::Default => score > z_threshold,
        }
    }
}
