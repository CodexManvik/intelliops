//! A per-metric online z-score baseline, plus the metric-kind policy on top.
//!
//! The arithmetic follows River's `stats.Mean` / `stats.Var` operation for
//! operation, so scores are bit-identical to the Python detector and a baseline
//! persisted by either can be loaded by the other.

mod policy;
mod zscore;

pub use policy::{MetricKind, Policy, Thresholds};
pub use zscore::{BaselineRow, Detector};
