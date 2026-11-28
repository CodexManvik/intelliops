//! A per-metric online z-score baseline, plus the metric-kind policy on top.
//!
//! The arithmetic follows River's `stats.Mean` / `stats.Var` operation for
//! operation: on one platform scores equal the Python detector's, across
//! platforms they agree to the last bit of the libm `pow`. A baseline persisted
//! by either can be loaded by the other.

mod policy;
mod zscore;

pub use policy::{MetricKind, Policy, Thresholds};
pub use zscore::{BaselineRow, Detector};
