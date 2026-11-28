//! Telemetry in, canonical events out. Push (`POST /ingest`) always works; a
//! Prometheus poll runs when `TELEMETRY_MODE=prometheus`.

mod normalize;
pub mod prometheus;
mod routes;

pub use normalize::normalize;
pub use routes::{parse_batch, routes};
