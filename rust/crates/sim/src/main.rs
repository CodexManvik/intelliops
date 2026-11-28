//! `INTELLIOPS_SIM` picks which workload this process is.

use std::path::PathBuf;
use std::process::ExitCode;

use intelliops_service::{Config, Failure, port, serve, start};
use intelliops_sim::apps::{Sim, demo_app, meridian};

#[tokio::main]
async fn main() -> ExitCode {
    start("sim", run).await
}

async fn run(config: Config) -> Result<(), Failure> {
    let name = config.get("sim", "demo-app");
    let ui = PathBuf::from(config.get("meridian_ui_dir", "ui"));
    let sim = Sim::new(&name, config);
    let app = match name.as_str() {
        "demo-app" => demo_app(sim),
        "meridian-gateway" => meridian(sim, Some(&ui)),
        "meridian-validation" | "meridian-aggregation" | "meridian-reporting" => {
            meridian(sim, None)
        }
        other => return Err(format!("unknown INTELLIOPS_SIM: {other:?}").into()),
    };
    serve(app, port()).await?;
    Ok(())
}
