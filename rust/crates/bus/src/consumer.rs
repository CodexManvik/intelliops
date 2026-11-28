//! Consumer groups with the delivery semantics of `common/bus.py`.
//!
//! A group is created at id 0 under the name the Python service used, so a
//! cut-over resumes where Python stopped. The consumer name must stay stable:
//! at-least-once re-serves the entries recorded against it.

use std::collections::VecDeque;
use std::time::Duration;

use intelliops_contracts::Envelope;
use redis::aio::MultiplexedConnection;
use redis::streams::{StreamReadOptions, StreamReadReply};
use redis::{AsyncCommands, AsyncConnectionConfig, from_redis_value};
use serde::de::DeserializeOwned;

use crate::BusError;

const BLOCK_MS: usize = 1000;
const BATCH: usize = 64;
const DRAIN_BATCH: usize = 100;
const RETRY_BACKOFF: Duration = Duration::from_secs(2);
const ATTEMPTS_TTL_SECONDS: i64 = 86_400;
const DONE: &str = "done";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConsumerConfig {
    pub topic: String,
    pub group: String,
    pub consumer: String,
    /// Acknowledge after handling instead of before.
    pub at_least_once: bool,
    /// Where undecodable or repeatedly failing entries are parked, if anywhere.
    pub dlq_suffix: Option<String>,
    pub max_delivery_attempts: u32,
    /// Skip events whose handler already finished once, remembered for this
    /// many seconds in Redis.
    pub dedupe_seconds: Option<u64>,
}

impl ConsumerConfig {
    #[must_use]
    pub fn new(topic: &str, group: &str) -> Self {
        Self {
            topic: topic.to_string(),
            group: group.to_string(),
            consumer: "c1".to_string(),
            at_least_once: false,
            dlq_suffix: None,
            max_delivery_attempts: 5,
            dedupe_seconds: None,
        }
    }
}

struct Entry {
    id: String,
    fields: Vec<(String, String)>,
}

/// Pull one decoded model at a time. Under at-least-once the previous entry is
/// acknowledged when the caller asks for the next one: that is the only proof
/// its handler finished.
pub struct Consumer {
    client: redis::Client,
    connection: Option<MultiplexedConnection>,
    config: ConsumerConfig,
    buffered: VecDeque<Entry>,
    in_flight: Option<String>,
    /// Dedupe key of the event being handled.
    claimed: Option<String>,
    /// Whether our own pending entries have been re-read since connecting.
    drained: bool,
}

impl Consumer {
    pub(crate) fn new(url: &str, config: ConsumerConfig) -> Result<Self, BusError> {
        Ok(Self {
            client: redis::Client::open(url)?,
            connection: None,
            drained: !config.at_least_once,
            config,
            buffered: VecDeque::new(),
            in_flight: None,
            claimed: None,
        })
    }

    async fn connect(&mut self) -> Result<MultiplexedConnection, BusError> {
        if let Some(connection) = &self.connection {
            return Ok(connection.clone());
        }
        // Blocking reads must outlast the default response timeout.
        let config = AsyncConnectionConfig::new().set_response_timeout(None);
        let mut connection = self
            .client
            .get_multiplexed_async_connection_with_config(&config)
            .await?;
        let created: Result<(), redis::RedisError> = connection
            .xgroup_create_mkstream(&self.config.topic, &self.config.group, "0")
            .await;
        if let Err(error) = created
            && error.code() != Some("BUSYGROUP")
        {
            return Err(error.into());
        }
        self.connection = Some(connection.clone());
        Ok(connection)
    }

    async fn fill(&mut self) -> Result<(), BusError> {
        let mut connection = self.connect().await?;
        let draining = !self.drained;
        let mut options = StreamReadOptions::default()
            .group(&self.config.group, &self.config.consumer)
            .count(if draining { DRAIN_BATCH } else { BATCH });
        if !draining {
            options = options.block(BLOCK_MS);
        }
        let start = if draining { "0" } else { ">" };
        let reply: Option<StreamReadReply> = connection
            .xread_options(&[&self.config.topic], &[start], &options)
            .await?;
        self.drained = true;
        for key in reply.into_iter().flat_map(|reply| reply.keys) {
            for entry in key.ids {
                let fields = entry
                    .map
                    .into_iter()
                    .filter_map(|(name, value)| Some((name, from_redis_value(value).ok()?)))
                    .collect();
                self.buffered.push_back(Entry {
                    id: entry.id,
                    fields,
                });
            }
        }
        // At-most-once acknowledges what it is about to hand out. A crash can
        // lose up to one batch (ADR-010).
        if !self.config.at_least_once && !self.buffered.is_empty() {
            let ids: Vec<&str> = self.buffered.iter().map(|e| e.id.as_str()).collect();
            let _: u64 = connection
                .xack(&self.config.topic, &self.config.group, &ids)
                .await?;
        }
        Ok(())
    }

    fn attempts_key(&self, id: &str) -> String {
        format!(
            "bus:attempts:{}:{}:{id}",
            self.config.group, self.config.topic
        )
    }

    async fn acknowledge(&mut self, id: &str) -> Result<(), BusError> {
        let mut connection = self.connect().await?;
        let _: u64 = connection
            .xack(&self.config.topic, &self.config.group, &[id])
            .await?;
        if self.config.dlq_suffix.is_some() {
            let _: u64 = connection.del(self.attempts_key(id)).await?;
        }
        Ok(())
    }

    /// Park `entry` on the dead-letter stream. `false` when there is none.
    async fn park(&mut self, entry: &Entry, reason: &str) -> Result<bool, BusError> {
        let Some(suffix) = self.config.dlq_suffix.clone() else {
            return Ok(false);
        };
        let mut connection = self.connect().await?;
        let mut fields: Vec<(&str, &str)> = entry
            .fields
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        fields.push(("_dlq_reason", reason));
        fields.push(("_dlq_topic", &self.config.topic));
        fields.push(("_dlq_group", &self.config.group));
        let _: String = connection
            .xadd(format!("{}{suffix}", self.config.topic), "*", &fields)
            .await?;
        Ok(true)
    }

    async fn exhausted(&mut self, entry: &Entry) -> Result<bool, BusError> {
        if !self.config.at_least_once || self.config.dlq_suffix.is_none() {
            return Ok(false);
        }
        let key = self.attempts_key(&entry.id);
        let mut connection = self.connect().await?;
        let attempts: u32 = connection.incr(&key, 1).await?;
        let _: bool = connection.expire(&key, ATTEMPTS_TTL_SECONDS).await?;
        if attempts <= self.config.max_delivery_attempts {
            return Ok(false);
        }
        self.park(entry, "max-delivery-attempts").await
    }

    /// Whether this event was already handled to the end. An unfinished
    /// earlier claim does not count, or a crash would lose the event.
    async fn already_done(&mut self, event_id: &str) -> Result<bool, BusError> {
        let Some(ttl) = self.config.dedupe_seconds else {
            return Ok(false);
        };
        let key = format!("idem:{}:{event_id}", self.config.group);
        let mut connection = self.connect().await?;
        let claimed: bool = redis::cmd("SET")
            .arg(&key)
            .arg("1")
            .arg("NX")
            .arg("EX")
            .arg(ttl)
            .query_async::<Option<String>>(&mut connection)
            .await?
            .is_some();
        if !claimed {
            let state: Option<String> = connection.get(&key).await?;
            if state.as_deref() == Some(DONE) {
                return Ok(true);
            }
        }
        self.claimed = Some(key);
        Ok(false)
    }

    /// The caller came back, so the previous handler finished.
    async fn settle(&mut self) -> Result<(), BusError> {
        if let (Some(key), Some(ttl)) = (self.claimed.clone(), self.config.dedupe_seconds) {
            let mut connection = self.connect().await?;
            let () = connection.set_ex(&key, DONE, ttl).await?;
            self.claimed = None;
        }
        if let Some(id) = self.in_flight.clone() {
            self.acknowledge(&id).await?;
            self.in_flight = None;
        }
        Ok(())
    }

    async fn try_next<T: DeserializeOwned>(&mut self) -> Result<Option<T>, BusError> {
        self.settle().await?;
        if self.buffered.is_empty() {
            self.fill().await?;
        }
        let Some(entry) = self.buffered.pop_front() else {
            return Ok(None);
        };
        if self.exhausted(&entry).await? {
            self.acknowledge(&entry.id).await?;
            return Ok(None);
        }
        let fields = entry.fields.iter().map(|(k, v)| (k.as_str(), v.as_str()));
        let decoded = Envelope::from_fields(fields)
            .and_then(|envelope| Ok((envelope.decode()?, envelope.id)));
        match decoded {
            Ok((model, event_id)) => {
                if let Some(event_id) = event_id
                    && self.already_done(&event_id).await?
                {
                    if self.config.at_least_once {
                        self.acknowledge(&entry.id).await?;
                    }
                    return Ok(None);
                }
                if self.config.at_least_once {
                    self.in_flight = Some(entry.id);
                }
                Ok(Some(model))
            }
            Err(error) => {
                // One bad message must not stop the stream.
                let parked = self.park(&entry, "decode").await?;
                tracing::warn!(%error, id = %entry.id, parked, topic = %self.config.topic,
                    "skipping an undecodable message");
                if self.config.at_least_once {
                    self.acknowledge(&entry.id).await?;
                }
                Ok(None)
            }
        }
    }

    /// The next model. Redis errors are retried forever, so a restart of Redis
    /// does not stop a consumer. Cancel safe only between calls.
    pub async fn next<T: DeserializeOwned>(&mut self) -> T {
        loop {
            match self.try_next().await {
                Ok(Some(model)) => return model,
                Ok(None) => {}
                Err(error) => {
                    tracing::warn!(%error, topic = %self.config.topic, "bus read failed; retrying");
                    // The group may be gone and the pending list survives.
                    self.connection = None;
                    self.buffered.clear();
                    self.drained = !self.config.at_least_once;
                    tokio::time::sleep(RETRY_BACKOFF).await;
                }
            }
        }
    }
}
