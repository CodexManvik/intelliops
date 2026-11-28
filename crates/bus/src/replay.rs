//! Bounded range reads and group repositioning, for rebuilding a projection.

use redis::streams::{StreamPendingCountReply, StreamRangeReply};
use redis::{AsyncCommands, from_redis_value};

use crate::{Bus, BusError};

/// `(id, fields)` of one stream entry.
pub type RawEntry = (String, Vec<(String, String)>);

/// Redis ids are `<ms>-<seq>`: compare them as numbers.
#[must_use]
pub fn id_order(id: &str) -> (u64, u64) {
    let (ms, seq) = id.split_once('-').unwrap_or((id, "0"));
    (ms.parse().unwrap_or(0), seq.parse().unwrap_or(0))
}

impl Bus {
    /// Up to `count` entries from `start` (inclusive; `(id` excludes it).
    pub async fn range(
        &self,
        topic: &str,
        start: &str,
        count: usize,
    ) -> Result<Vec<RawEntry>, BusError> {
        let mut connection = self.connection();
        let reply: StreamRangeReply = connection.xrange_count(topic, start, "+", count).await?;
        Ok(reply
            .ids
            .into_iter()
            .map(|entry| {
                let fields = entry
                    .map
                    .into_iter()
                    .filter_map(|(name, value)| Some((name, from_redis_value(value).ok()?)))
                    .collect();
                (entry.id, fields)
            })
            .collect())
    }

    /// The group's last delivered id, or `None` if there is no such group.
    pub async fn group_position(
        &self,
        topic: &str,
        group: &str,
    ) -> Result<Option<String>, BusError> {
        let mut connection = self.connection();
        let reply: Result<redis::streams::StreamInfoGroupsReply, _> =
            connection.xinfo_groups(topic).await;
        match reply {
            Ok(reply) => Ok(reply
                .groups
                .into_iter()
                .find(|g| g.name == group)
                .map(|g| g.last_delivered_id)),
            Err(error) if error.code() == Some("ERR") => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    /// Make the group resume after `last_id`, creating it if needed. It never
    /// moves backwards, and pending entries up to there are acknowledged.
    pub async fn advance_group(
        &self,
        topic: &str,
        group: &str,
        last_id: Option<&str>,
    ) -> Result<(), BusError> {
        let mut connection = self.connection();
        let created: Result<(), redis::RedisError> =
            connection.xgroup_create_mkstream(topic, group, "0").await;
        if let Err(error) = created
            && error.code() != Some("BUSYGROUP")
        {
            return Err(error.into());
        }
        if let Some(last_id) = last_id {
            let behind = self
                .group_position(topic, group)
                .await?
                .is_none_or(|position| id_order(last_id) > id_order(&position));
            if behind {
                let () = connection.xgroup_setid(topic, group, last_id).await?;
            }
        }
        let pending: StreamPendingCountReply = connection
            .xpending_count(topic, group, "-", "+", 1000)
            .await?;
        let ids: Vec<String> = pending.ids.into_iter().map(|p| p.id).collect();
        if !ids.is_empty() {
            let _: u64 = connection.xack(topic, group, &ids).await?;
        }
        Ok(())
    }
}
