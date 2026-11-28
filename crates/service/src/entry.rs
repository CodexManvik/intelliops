//! The shape every service binary has: `healthcheck`, or configure and run.

use std::future::Future;
use std::process::ExitCode;

use crate::{Config, healthcheck, init_logging, port};

pub type Failure = Box<dyn std::error::Error + Send + Sync>;

pub async fn start<F, Fut>(name: &str, run: F) -> ExitCode
where
    F: FnOnce(Config) -> Fut,
    Fut: Future<Output = Result<(), Failure>>,
{
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        return match healthcheck(port()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(_) => ExitCode::FAILURE,
        };
    }
    let config = Config::from_env();
    init_logging(&config);
    match run(config).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            tracing::error!(%error, service = name, "service stopped");
            ExitCode::FAILURE
        }
    }
}
