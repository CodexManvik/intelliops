use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use intelliops_ingestion::{prometheus, routes};
use intelliops_service::{Config, Failure, Service, app, bus, port, serve, start};

#[tokio::main]
async fn main() -> ExitCode {
    start("ingestion", run).await
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
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
