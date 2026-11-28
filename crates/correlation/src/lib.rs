//! Telemetry in, situations out.

mod config;
mod engine;
mod runtime;
mod scorer;

pub use config::engine_from;
pub use engine::{Engine, EngineConfig, Grouping, SuppressionMode, Training};
pub use runtime::Correlation;
pub use scorer::Scorer;
