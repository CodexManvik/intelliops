use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use intelliops_bus::Bus;
use intelliops_ingestion::{prometheus, routes};
use intelliops_service::{Config, Service, app, healthcheck, init_logging, serve};

const DEFAULT_PORT: u16 = 8000;
const DEFAULT_MAXLEN: usize = 100_000;

fn port() -> u16 {
    std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

#[tokio::main]
async fn main() -> ExitCode {
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
            tracing::error!(%error, "ingestion stopped");
            ExitCode::FAILURE
        }
    }
}

async fn run(config: Config) -> Result<(), Box<dyn std::error::Error>> {
    let bus = Bus::new(
        &config.get("redis_url", "redis://localhost:6379"),
        config.number("bus_stream_maxlen", DEFAULT_MAXLEN)?,
    )?;
    if config.get("telemetry_mode", "file") == "prometheus" {
        let source = prometheus::Source::new(
            &config.get("prometheus_url", "http://localhost:9090"),
            &config.get("prometheus_query", "cpu_usage"),
        )?;
        let interval = Duration::try_from_secs_f64(config.number("telemetry_poll_seconds", 5.0)?)?;
        tokio::spawn(prometheus::run(source, bus.clone(), interval));
    }
    let service = Arc::new(Service::new("ingestion-service", config, bus.clone()));
    serve(app(service, routes(bus)), port()).await?;
    Ok(())
}
