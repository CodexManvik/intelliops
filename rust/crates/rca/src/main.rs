use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::Arc;
use std::time::{Duration, Instant};

use intelliops_bus::{Bus, Consumer};
use intelliops_contracts::{AuditRecord, EnrichmentContext, Situation, Timestamp, topics};
use intelliops_embed::Embedder;
use intelliops_rca::explain::explain;
use intelliops_rca::rank::{Selector, diagnose};
use intelliops_rca::routes::{Explainer, Llm, chat_client};
use intelliops_rca::semantic::Semantic;
use intelliops_service::{
    Config, Failure, Service, app, bus, consumer_config, port, publish, serve, start, store,
};
use intelliops_store::Store;
use serde::de::DeserializeOwned;
use tokio::sync::RwLock;

const RETRY: Duration = Duration::from_secs(2);

#[tokio::main]
async fn main() -> ExitCode {
    start("rca", run).await
}

/// `(signature, runbook) -> worked/total`, re-read at most every `every`.
struct Reliability {
    every: Duration,
    loaded: Option<Instant>,
    table: HashMap<(String, String), f64>,
}

impl Reliability {
    async fn refresh(&mut self, store: &Store) {
        if self.loaded.is_some_and(|at| at.elapsed() < self.every) {
            return;
        }
        self.loaded = Some(Instant::now());
        match store.training_all().await {
            Ok(records) => {
                let mut counts: HashMap<(String, String), (u32, u32)> = HashMap::new();
                for r in records {
                    let entry = counts.entry((r.signature, r.playbook_id)).or_default();
                    entry.0 += u32::from(r.worked);
                    entry.1 += 1;
                }
                self.table = counts
                    .into_iter()
                    .map(|(key, (worked, total))| (key, f64::from(worked) / f64::from(total)))
                    .collect();
            }
            // Ranking falls back to the last table rather than failing.
            Err(error) => tracing::warn!(%error, "could not read the track record"),
        }
    }
}

/// A deploy, topology or config file, standing in for a CMDB.
fn read_json<T: DeserializeOwned + Default>(dir: &Path, name: &str) -> T {
    let path = dir.join(name);
    let Ok(raw) = std::fs::read_to_string(&path) else {
        return T::default();
    };
    serde_json::from_str(&raw).unwrap_or_else(|error| {
        tracing::warn!(%error, path = %path.display(), "unreadable context file");
        T::default()
    })
}

struct Rca {
    bus: Bus,
    store: Store,
    context_dir: PathBuf,
    selector: Option<Arc<Semantic>>,
    llm: Llm,
}

impl Rca {
    async fn consume(self, mut consumer: Consumer, mut reliability: Reliability) {
        loop {
            let situation: Situation = consumer.next().await;
            reliability.refresh(&self.store).await;
            let playbooks = loop {
                match self.store.playbook_list().await {
                    Ok(playbooks) => break playbooks,
                    Err(error) => {
                        tracing::warn!(%error, "cannot read playbooks; retrying");
                        tokio::time::sleep(RETRY).await;
                    }
                }
            };
            let context = EnrichmentContext {
                recent_deploys: read_json(&self.context_dir, "deploys.json"),
                topology: read_json(&self.context_dir, "topology.json"),
                config_changes: read_json(&self.context_dir, "config_changes.json"),
            };
            let selector = self.selector.clone();
            let table = reliability.table.clone();
            let enriched = context.clone();
            // Embedding is CPU work: keep it off the async threads.
            let diagnosed = tokio::task::spawn_blocking(move || {
                let reliability = |signature: &str, runbook: &str| {
                    table
                        .get(&(signature.to_string(), runbook.to_string()))
                        .copied()
                        .unwrap_or(0.0)
                };
                diagnose(
                    situation,
                    &enriched,
                    &playbooks,
                    selector.as_deref().map(|s| s as &dyn Selector),
                    Some(&reliability),
                )
            })
            .await;
            let mut diagnosed = match diagnosed {
                Ok(diagnosed) => diagnosed,
                Err(error) => {
                    tracing::error!(%error, "diagnosis failed");
                    continue;
                }
            };
            let chat = self.llm.explainer.read().await.chat.clone();
            if let Some(top) = diagnosed.hypotheses.first_mut() {
                let (text, source) =
                    explain(chat.as_ref(), top, &context, &diagnosed.situation).await;
                top.explanation = Some(text);
                top.explanation_source = Some(source.to_string());
            }
            publish(&self.bus, topics::SITUATIONS_DIAGNOSED, &diagnosed).await;
            let id = &diagnosed.situation.id;
            let record = AuditRecord {
                actor: "rca-service".into(),
                action: "diagnose".into(),
                resource: format!("situation:{id}"),
                decision: "allow".into(),
                ts: Timestamp::now(),
                correlation_id: id.clone(),
            };
            while let Err(error) = self.store.audit_write(&record).await {
                tracing::error!(%error, "audit write failed; retrying");
                tokio::time::sleep(RETRY).await;
            }
        }
    }
}

fn selector(config: &Config) -> Result<Option<Arc<Semantic>>, Failure> {
    if config.get("runbook_selector_mode", "off") != "embedding" {
        return Ok(None);
    }
    let dir = PathBuf::from(config.get("model_dir", "models/minilm"));
    let threshold = config.number("runbook_selector_threshold", 0.45)?;
    // A missing model fails the start: silently ranking by rules alone would
    // look like the feature working.
    Ok(Some(Arc::new(Semantic::new(
        Embedder::load(&dir)?,
        threshold,
    ))))
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
    let store = store(&config).await?;
    let llm = Llm {
        explainer: Arc::new(RwLock::new(Explainer {
            chat: chat_client(
                &config.get("llm_explanation_endpoint", ""),
                &config.get("llm_explanation_model", "gpt-4o-mini"),
                &config.get("llm_explanation_api_key", ""),
                config.number("llm_explanation_timeout_seconds", 10.0)?,
            ),
            ..Explainer::default()
        })),
        default_model: config.get("llm_explanation_model", "gpt-4o-mini"),
    };
    let rca = Rca {
        bus: bus.clone(),
        store: store.clone(),
        context_dir: PathBuf::from(config.get("rca_context_path", "data/rca_context")),
        selector: selector(&config)?,
        llm: llm.clone(),
    };
    let reliability = Reliability {
        every: Duration::try_from_secs_f64(config.number("reliability_refresh_seconds", 60.0)?)?,
        loaded: None,
        table: HashMap::new(),
    };
    let situations = consumer_config(&config, topics::SITUATIONS_DETECTED, "rca")?;
    tokio::spawn(rca.consume(bus.consumer(situations)?, reliability));

    let service = Service::new("rca-service", config, bus).with_database(store);
    serve(app(Arc::new(service), llm.routes()), port()).await?;
    Ok(())
}
