//! A per-metric online z-score baseline, plus the metric-kind policy on top.
//!
//! The arithmetic follows River's `stats.Mean` / `stats.Var` operation for
//! operation: on one platform scores equal the Python detector's, across
//! platforms they differ only by the rounding of the libm `pow`. A baseline persisted
//! by either can be loaded by the other.

mod forest;
mod policy;
mod robust;
mod zscore;

pub use forest::{MIN_FIT_SAMPLES, Model, Sighting};
pub use policy::{MetricKind, Policy, Thresholds};
pub use robust::{Robust, RobustConfig};
pub use zscore::{BaselineRow, Detector};
