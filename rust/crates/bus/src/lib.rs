//! Redis Streams: publishing and consumer groups.

mod consumer;
mod replay;

pub use consumer::{Consumer, ConsumerConfig};
pub use replay::{RawEntry, id_order};

use std::time::Duration;

use intelliops_contracts::{Envelope, EnvelopeError};
use redis::aio::{ConnectionManager, ConnectionManagerConfig};
use redis::streams::StreamMaxlen;
use serde::Serialize;

#[derive(Debug, thiserror::Error)]
pub enum BusError {
    #[error("redis: {0}")]
    Redis(#[from] redis::RedisError),
    #[error(transparent)]
    Envelope(#[from] EnvelopeError),
}

const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(10);

/// A cheaply cloneable handle. It connects on first use and reconnects by
/// itself, so a service starts even while Redis is still coming up.
#[derive(Clone)]
pub struct Bus {
    url: String,
    connection: ConnectionManager,
    stream_maxlen: usize,
}

impl Bus {
    /// `stream_maxlen` caps each stream approximately; 0 leaves it unbounded.
    pub fn new(url: &str, stream_maxlen: usize) -> Result<Self, BusError> {
        let client = redis::Client::open(url)?;
        let config = ConnectionManagerConfig::new()
            .set_connection_timeout(Some(CONNECT_TIMEOUT))
            .set_response_timeout(Some(RESPONSE_TIMEOUT))
            .set_number_of_retries(1);
        Ok(Self {
            url: url.to_string(),
            connection: ConnectionManager::new_lazy_with_config(client, config)?,
            stream_maxlen,
        })
    }

    /// Publish every model to `topic` in one round trip, in order.
    pub async fn publish_all<T: Serialize>(
        &self,
        topic: &str,
        models: &[T],
    ) -> Result<(), BusError> {
        if models.is_empty() {
            return Ok(());
        }
        let mut pipeline = redis::pipe();
        for model in models {
            let id = uuid::Uuid::new_v4().simple().to_string();
            let envelope = Envelope::encode(model, id.clone())?;
            let command = pipeline.cmd("XADD").arg(topic);
            if self.stream_maxlen > 0 {
                command.arg(StreamMaxlen::Approx(self.stream_maxlen));
            }
            command
                .arg("*")
                .arg("data")
                .arg(envelope.data)
                .arg("id")
                .arg(id)
                .ignore();
        }
        pipeline
            .query_async::<()>(&mut self.connection.clone())
            .await?;
        Ok(())
    }

    /// A consumer with a connection of its own, since its reads block.
    pub fn consumer(&self, config: ConsumerConfig) -> Result<Consumer, BusError> {
        Consumer::new(&self.url, config)
    }

    pub(crate) fn connection(&self) -> ConnectionManager {
        self.connection.clone()
    }

    /// `true` for the first claimant of `key`.
    pub async fn claim(&self, key: &str, ttl_seconds: u64) -> Result<bool, BusError> {
        let reply: Option<String> = redis::cmd("SET")
            .arg(format!("idem:{key}"))
            .arg("1")
            .arg("NX")
            .arg("EX")
            .arg(ttl_seconds)
            .query_async(&mut self.connection())
            .await?;
        Ok(reply.is_some())
    }

    pub async fn state(&self, key: &str) -> Result<Option<String>, BusError> {
        Ok(redis::cmd("GET")
            .arg(format!("idem:{key}"))
            .query_async(&mut self.connection())
            .await?)
    }

    pub async fn set_state(
        &self,
        key: &str,
        value: &str,
        ttl_seconds: u64,
    ) -> Result<(), BusError> {
        let () = redis::cmd("SET")
            .arg(format!("idem:{key}"))
            .arg(value)
            .arg("EX")
            .arg(ttl_seconds)
            .query_async(&mut self.connection())
            .await?;
        Ok(())
    }

    pub async fn ping(&self) -> Result<(), BusError> {
        redis::cmd("PING")
            .query_async::<()>(&mut self.connection.clone())
            .await?;
        Ok(())
    }
}
