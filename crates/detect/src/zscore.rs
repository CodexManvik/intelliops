use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Welford/West running mean and sum of squared differences.
#[derive(Debug, Clone, Copy, Default)]
struct Running {
    n: f64,
    mean: f64,
    sum_sq: f64,
}

impl Running {
    fn update(&mut self, x: f64) {
        let old = self.mean;
        self.n += 1.0;
        self.mean += (1.0 / self.n) * (x - self.mean);
        self.sum_sq += (x - old) * (x - self.mean);
    }

    /// Sample variance (ddof = 1).
    fn variance(&self) -> f64 {
        if self.n > 1.0 {
            self.sum_sq / (self.n - 1.0)
        } else {
            0.0
        }
    }

    /// `powf(0.5)`, not `sqrt()`: Python computes `var ** 0.5`.
    fn std_dev(&self) -> f64 {
        self.variance().powf(0.5)
    }
}

#[derive(Debug, Clone, Copy, Default)]
struct Series {
    stats: Running,
    seen: u64,
}

/// A persisted baseline row, matching the `correlation_baseline` table.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BaselineRow {
    pub metric_name: String,
    pub n: f64,
    pub mean: f64,
    pub variance: f64,
    pub count: u64,
}

#[derive(Debug, Clone)]
pub struct Detector {
    warmup_samples: u64,
    series: BTreeMap<String, Series>,
}

impl Detector {
    #[must_use]
    pub fn new(warmup_samples: u64) -> Self {
        Self {
            warmup_samples,
            series: BTreeMap::new(),
        }
    }

    /// Score `value` against the baseline so far, then fold it in. Scores 0
    /// until the metric has warmed up, because an early variance is unstable.
    pub fn detect(&mut self, metric: &str, value: Option<f64>) -> f64 {
        let Some(value) = value else { return 0.0 };
        // Look up before inserting so the steady state does not allocate.
        if !self.series.contains_key(metric) {
            self.series.insert(metric.to_string(), Series::default());
        }
        let Some(series) = self.series.get_mut(metric) else {
            return 0.0; // INFALLIBLE: inserted above
        };
        let mean = series.stats.mean;
        let std_dev = series.stats.std_dev();
        let score = if series.seen < self.warmup_samples || std_dev == 0.0 {
            0.0
        } else {
            (value - mean).abs() / std_dev
        };
        series.stats.update(value);
        series.seen += 1;
        score
    }

    /// `(mean, standard deviation)` of a metric, for annotating a situation.
    #[must_use]
    pub fn baseline(&self, metric: &str) -> Option<(f64, f64)> {
        let series = self.series.get(metric)?;
        Some((series.stats.mean, series.stats.std_dev()))
    }

    #[must_use]
    pub fn snapshot(&self) -> Vec<BaselineRow> {
        self.series
            .iter()
            .map(|(name, series)| BaselineRow {
                metric_name: name.clone(),
                n: series.stats.n,
                mean: series.stats.mean,
                variance: series.stats.variance(),
                count: series.seen,
            })
            .collect()
    }

    pub fn load(&mut self, rows: &[BaselineRow]) {
        for row in rows {
            let stats = Running {
                n: row.n,
                mean: row.mean,
                sum_sq: (row.n - 1.0) * row.variance,
            };
            self.series.insert(
                row.metric_name.clone(),
                Series {
                    stats,
                    seen: row.count,
                },
            );
        }
    }

    pub fn reset(&mut self) {
        self.series.clear();
    }
}
