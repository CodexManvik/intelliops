use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use intelliops_bus::Consumer;
use intelliops_contracts::{DecisionOutcome, RemediationOutcome, RemediationResult, topics};
use intelliops_governance::author::Author;
use intelliops_governance::context::SystemContext;
use intelliops_governance::rbac::Policy;
use intelliops_governance::{Governance, Hub};
use intelliops_llm::ChatClient;
use intelliops_service::{
    Config, Failure, Service, app, bus, consumer_config, port, serve, start, store,
};
use intelliops_store::Store;

#[tokio::main]
async fn main() -> ExitCode {
    start("governance", run).await
}

/// Write each outcome back onto the author's memory of the draft it came from.
async fn remember(store: Store, mut consumer: Consumer) {
    loop {
        let outcome: RemediationOutcome = consumer.next().await;
        // An escalation never ran, so it says nothing about a draft.
        let result = match outcome.result {
            RemediationResult::Escalated => continue,
            RemediationResult::Success => DecisionOutcome::Worked,
            RemediationResult::Failure | RemediationResult::RolledBack => DecisionOutcome::Failed,
        };
        if let Err(error) = store.decision_outcome(&outcome.playbook_id, result).await {
            tracing::warn!(%error, "could not record the draft's outcome");
        }
    }
}

fn author(config: &Config) -> Result<Option<Arc<Author>>, Failure> {
    let endpoint = config.get("llm_runbook_endpoint", "");
    if config.get("runbook_author_mode", "off") != "openai" || endpoint.is_empty() {
        return Ok(None);
    }
    let timeout = Duration::try_from_secs_f64(config.number("llm_runbook_timeout_seconds", 10.0)?)?;
    Ok(Some(Arc::new(Author {
        chat: ChatClient::new(
            &endpoint,
            &config.get("llm_runbook_model", "gpt-4o-mini"),
            &config.get("llm_runbook_api_key", ""),
            timeout,
        )?,
    })))
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
    let store = store(&config).await?;
    let policy = Policy::load(&PathBuf::from(
        config.get("rbac_policy_path", "policies/rbac_policy.yaml"),
    ))?;
    let token_mode = config.get("auth_mode", "off") == "token";
    let configured = config.get("auth_token", "");
    let governance = Governance {
        store: store.clone(),
        policy: Arc::new(policy),
        author: author(&config)?,
        context: SystemContext(PathBuf::from(
            config.get("system_context_path", "config/system_context.yaml"),
        )),
        hub: Hub::default(),
        // No configured token must mean nobody gets in, not everybody.
        stream_token: token_mode.then(|| {
            if configured.is_empty() {
                uuid::Uuid::new_v4().to_string()
            } else {
                configured
            }
        }),
    };

    let outcomes = consumer_config(&config, topics::REMEDIATION_OUTCOMES, "governance")?;
    tokio::spawn(remember(store.clone(), bus.consumer(outcomes)?));

    let service = Service::new("governance-service", config, bus)
        .with_database(store)
        .with_open_paths(|method, path| {
            method == "GET" && path.starts_with("/agent-runs/") && path.ends_with("/stream")
        });
    serve(app(Arc::new(service), governance.routes()), port()).await?;
    Ok(())
}
