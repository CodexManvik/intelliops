//! The shared vocabulary every IntelliOps service uses over the bus.
//!
//! Wire-compatible with `common/contracts.py` in both directions, so Python and
//! Rust services can run side by side during the port.

mod envelope;
mod identity;
mod models;
mod time;

pub use envelope::{Envelope, EnvelopeError};
pub use identity::{fingerprint, signature, signature_from_situation_id, situation_id};
pub use models::*;
pub use time::Timestamp;

/// Bus topic names.
pub mod topics {
    pub const TELEMETRY_RAW: &str = "telemetry.raw";
    pub const SITUATIONS_DETECTED: &str = "situations.detected";
    pub const SITUATIONS_SUPPRESSED: &str = "situations.suppressed";
    pub const SITUATIONS_DIAGNOSED: &str = "situations.diagnosed";
    pub const REMEDIATION_OUTCOMES: &str = "remediation.outcomes";
}
