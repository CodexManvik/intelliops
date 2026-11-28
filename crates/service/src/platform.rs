//! How a platform service builds its bus, consumers and store from config.

use std::path::Path;
use std::time::Duration;

use intelliops_bus::{Bus, BusError, ConsumerConfig};
use intelliops_store::{Store, StoreError, load_seed_playbooks};
use serde::Serialize;

use crate::{Config, Failure};

const DEFAULT_PORT: u16 = 8000;
const PUBLISH_BACKOFF: Duration = Duration::from_secs(2);

#[must_use]
pub fn port() -> u16 {
    std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(DEFAULT_PORT)
}

pub fn bus(config: &Config) -> Result<Bus, Failure> {
    Ok(Bus::new(
        &config.get("redis_url", "redis://localhost:6379"),
        config.number("bus_stream_maxlen", 100_000usize)?,
    )?)
}

/// The `INTELLIOPS_BUS_*` delivery settings for one topic and group.
pub fn consumer_config(
    config: &Config,
    topic: &str,
    group: &str,
) -> Result<ConsumerConfig, String> {
    let mut consumer = ConsumerConfig::new(topic, group);
    let name = config.get("bus_consumer_name", "");
    if !name.is_empty() {
        consumer.consumer = name;
    }
    consumer.at_least_once = config.get("bus_delivery", "at_most_once") == "at_least_once";
    if config.get("bus_dlq_mode", "off") == "on" {
        consumer.dlq_suffix = Some(config.get("bus_dlq_topic_suffix", ".dlq"));
    }
    consumer.max_delivery_attempts = config.number("bus_max_delivery_attempts", 5)?;
    if config.get("bus_idempotency_mode", "off") != "off" {
        consumer.dedupe_seconds = Some(config.number("bus_idempotency_ttl_seconds", 86_400)?);
    }
    Ok(consumer)
}

/// The configured store, with seed playbooks loaded. Preparing the database is
/// best effort at boot: a service starts, reports not ready, and recovers.
pub async fn store(config: &Config) -> Result<Store, StoreError> {
    let store = if config.get("store_backend", "file") == "postgres" {
        Store::postgres(&config.get(
            "database_url",
            "postgresql://intelliops:intelliops@localhost:5432/intelliops",
        ))?
    } else {
        Store::memory()
    };
    let seeds = load_seed_playbooks(Path::new(
        &config.get("playbook_store_path", "data/playbooks"),
    ))?;
    let prepared = async {
        store.migrate().await?;
        store.playbook_seed(&seeds).await
    };
    if let Err(error) = prepared.await {
        tracing::warn!(%error, "database is not ready at boot");
    }
    Ok(store)
}

/// Publish, retrying until the bus takes it. A consumer that gave up here
/// would acknowledge an event whose result was never recorded.
pub async fn publish<T: Serialize + Sync>(bus: &Bus, topic: &str, model: &T) {
    loop {
        match bus.publish_all(topic, std::slice::from_ref(model)).await {
            Ok(()) => return,
            Err(BusError::Envelope(error)) => {
                tracing::error!(%error, topic, "model cannot be serialized; dropping it");
                return;
            }
            Err(error) => {
                tracing::warn!(%error, topic, "publish failed; retrying");
                tokio::time::sleep(PUBLISH_BACKOFF).await;
            }
        }
    }
}
