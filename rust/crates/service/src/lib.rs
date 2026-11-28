//! The Rust counterpart of `services/base.py`.

mod config;
mod entry;
mod http;
mod platform;
mod run;

pub use config::Config;
pub use entry::{Failure, start};
pub use http::{Service, app};
pub use platform::{bus, consumer_config, port, publish, store};
pub use run::{healthcheck, init_logging, serve, shutdown_signal};

/// The current time, for services that do not otherwise need the contracts.
#[must_use]
pub fn now_rfc3339() -> String {
    intelliops_contracts::Timestamp::now().to_string()
}
