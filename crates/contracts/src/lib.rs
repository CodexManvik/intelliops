//! The shared vocabulary every IntelliOps service uses over the bus.
//!
//! The wire format is the one the original Python services used, so recorded
//! traffic from them still decodes.

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
