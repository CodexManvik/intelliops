//! Median / MAD over a bounded window per (metric, series, hour bucket). One
//! earlier spike barely moves a median, where it would shift a mean.

use std::collections::{BTreeMap, VecDeque};

use indexmap::IndexMap;

/// MAD to sigma consistency constant for normal data.
const MAD_C: f64 = 1.4826;
/// Score floor for a real step off a perfectly flat baseline.
const FLAT_STEP_SCORE: f64 = 6.0;
/// A flat baseline gives no scale, so a step must be this share of its magnitude.
const FLAT_MIN_RELATIVE: f64 = 0.5;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RobustConfig {
    pub warmup_samples: usize,
    pub seasonal_buckets: u32,
    pub window_size: usize,
    /// One window per (metric, series) instead of pooling every series.
    pub per_series: bool,
}

impl Default for RobustConfig {
    fn default() -> Self {
        Self {
            warmup_samples: 30,
            seasonal_buckets: 24,
            window_size: 128,
            per_series: false,
        }
    }
}

type Key = (String, String, u32);

#[derive(Debug, Clone)]
pub struct Robust {
    config: RobustConfig,
    /// Insertion order matters: it breaks ties when borrowing a warm window.
    windows: IndexMap<Key, VecDeque<f64>>,
}

/// Median as numpy computes it: NaN if any sample is NaN.
fn median(samples: &mut [f64]) -> f64 {
    if samples.is_empty() || samples.iter().any(|v| v.is_nan()) {
        return f64::NAN;
    }
    samples.sort_unstable_by(f64::total_cmp);
    let middle = samples.len() / 2;
    let upper = samples.get(middle).copied().unwrap_or(f64::NAN);
    if samples.len() % 2 == 1 {
        upper
    } else {
        let lower = samples.get(middle - 1).copied().unwrap_or(f64::NAN);
        (lower + upper) / 2.0
    }
}

/// `(median, median absolute deviation)`.
fn median_mad(mut samples: Vec<f64>) -> (f64, f64) {
    let centre = median(&mut samples);
    for sample in &mut samples {
        *sample = (*sample - centre).abs();
    }
    (centre, median(&mut samples))
}

impl Robust {
    #[must_use]
    pub fn new(config: RobustConfig) -> Self {
        Self {
            config,
            windows: IndexMap::new(),
        }
    }

    /// While this hour's window is warming, borrow the fullest warm window of
    /// the same series, so the detector is not blind after every hour change.
    fn reference(&self, key: &Key) -> Option<&VecDeque<f64>> {
        let own = self.windows.get(key)?;
        if own.len() >= self.config.warmup_samples {
            return Some(own);
        }
        let mut best = own;
        for (other_key, other) in &self.windows {
            let same = other_key.0 == key.0 && (!self.config.per_series || other_key.1 == key.1);
            if same && other.len() >= self.config.warmup_samples && other.len() > best.len() {
                best = other;
            }
        }
        Some(best)
    }

    /// Score against the window so far, then add the value to it.
    pub fn detect(&mut self, metric: &str, series: &str, hour: u32, value: Option<f64>) -> f64 {
        let Some(value) = value else { return 0.0 };
        let series = if self.config.per_series { series } else { "" };
        let bucket = hour % self.config.seasonal_buckets.max(1);
        let key = (metric.to_string(), series.to_string(), bucket);
        self.windows.entry(key.clone()).or_default();
        let score = match self.reference(&key) {
            Some(window) if window.len() >= self.config.warmup_samples => {
                let (centre, mad) = median_mad(window.iter().copied().collect());
                let deviation = (value - centre).abs();
                // `f64::max` ignores NaN where Python's `max` does not, but a
                // NaN centre never reaches these two branches with a NaN scale.
                let scale = centre.abs().max(1.0);
                if mad > 0.0 {
                    deviation / (MAD_C * mad)
                } else if deviation <= FLAT_MIN_RELATIVE * scale {
                    0.0
                } else {
                    FLAT_STEP_SCORE.max(deviation / scale)
                }
            }
            _ => 0.0,
        };
        if let Some(window) = self.windows.get_mut(&key) {
            window.push_back(value);
            while window.len() > self.config.window_size {
                window.pop_front();
            }
        }
        score
    }

    /// `metric -> (median, MAD as sigma)`, pooled across hours and series.
    #[must_use]
    pub fn baselines(&self) -> BTreeMap<String, (f64, f64)> {
        let mut pooled: BTreeMap<&str, Vec<f64>> = BTreeMap::new();
        for (key, window) in &self.windows {
            if !window.is_empty() {
                pooled.entry(&key.0).or_default().extend(window);
            }
        }
        pooled
            .into_iter()
            .map(|(name, samples)| {
                let (centre, mad) = median_mad(samples);
                (name.to_string(), (centre, mad * MAD_C))
            })
            .collect()
    }

    /// Samples held per metric.
    #[must_use]
    pub fn counts(&self) -> BTreeMap<String, usize> {
        let mut counts = BTreeMap::new();
        for (key, window) in &self.windows {
            *counts.entry(key.0.clone()).or_insert(0) += window.len();
        }
        counts
    }

    pub fn reset(&mut self) {
        self.windows.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_step_off_a_flat_baseline_is_an_anomaly_but_a_wobble_is_not() {
        let mut robust = Robust::new(RobustConfig {
            warmup_samples: 3,
            ..RobustConfig::default()
        });
        for _ in 0..3 {
            robust.detect("up", "", 1, Some(18.0));
        }
        assert_eq!(robust.detect("up", "", 1, Some(17.34)), 0.0);
        assert!(robust.detect("up", "", 1, Some(0.0)) > 0.0);
    }

    #[test]
    fn a_new_hour_borrows_a_warm_window() {
        let mut robust = Robust::new(RobustConfig {
            warmup_samples: 3,
            ..RobustConfig::default()
        });
        for v in [10.0, 11.0, 12.0, 10.0] {
            robust.detect("m", "", 1, Some(v));
        }
        assert!(robust.detect("m", "", 2, Some(100.0)) > 3.0);
    }
}
