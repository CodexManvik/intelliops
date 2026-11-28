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
