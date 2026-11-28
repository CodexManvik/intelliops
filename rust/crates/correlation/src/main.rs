use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use intelliops_contracts::topics;
use intelliops_correlation::{Correlation, engine_from};
use intelliops_service::{
    Config, Failure, Service, app, bus, consumer_config, port, serve, start, store,
};
use tokio::sync::Mutex;

#[tokio::main]
async fn main() -> ExitCode {
    start("correlation", run).await
}

fn seconds(config: &Config, name: &str, default: f64) -> Result<Duration, Failure> {
    Ok(Duration::try_from_secs_f64(config.number(name, default)?)?)
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
    let correlation = Correlation {
        engine: Arc::new(Mutex::new(engine_from(&config)?)),
        store: store(&config).await?,
        bus: bus.clone(),
        kind: config.get("correlator_kind", "river"),
    };
    // Restore before consuming, so the first events meet a warm baseline.
    correlation.restore_baseline().await;
    correlation.refresh_reliability().await;

    let events = consumer_config(&config, topics::TELEMETRY_RAW, "correlation")?;
    tokio::spawn(correlation.clone().consume(bus.consumer(events)?));
    let window = seconds(&config, "correlation_window_seconds", 30.0)?;
    tokio::spawn(correlation.clone().flush_periodically(
        window.max(Duration::from_millis(100)),
        seconds(&config, "baseline_snapshot_seconds", 30.0)?,
        seconds(&config, "reliability_refresh_seconds", 60.0)?,
    ));

    let service =
        Service::new("correlation-service", config, bus).with_database(correlation.store.clone());
    // `serve` returns on SIGTERM; then publish what is still buffered.
    serve(app(Arc::new(service), correlation.clone().routes()), port()).await?;
    correlation.flush().await;
    Ok(())
}
