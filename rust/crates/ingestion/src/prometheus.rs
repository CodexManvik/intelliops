//! Poll a Prometheus instant query. Anything unusable yields no events, so the
//! loop survives Prometheus not being ready.

use std::time::Duration;

use intelliops_bus::Bus;
use intelliops_contracts::{TelemetryEvent, TelemetryKind, Timestamp, fingerprint, topics};
use serde_json::Value;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(5);

pub struct Source {
    client: reqwest::Client,
    url: String,
    query: String,
}

impl Source {
    pub fn new(base_url: &str, query: &str) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: reqwest::Client::builder()
                .timeout(REQUEST_TIMEOUT)
                .build()?,
            url: format!("{}/api/v1/query", base_url.trim_end_matches('/')),
            query: query.to_string(),
        })
    }

    pub async fn poll(&self) -> Vec<TelemetryEvent> {
        let request = self.client.get(&self.url).query(&[("query", &self.query)]);
        let response = match request.send().await {
            Ok(response) if response.status().is_success() => response,
            Ok(response) => {
                tracing::info!(status = %response.status(), "prometheus query failed");
                return Vec::new();
            }
            Err(error) => {
                tracing::info!(%error, "prometheus unreachable; will retry next poll");
                return Vec::new();
            }
        };
        match response.json::<Value>().await {
            Ok(body) => events_from(&body),
            Err(error) => {
                tracing::info!(%error, "prometheus returned an unusable body");
                Vec::new()
            }
        }
    }
}

/// Map a query response to events, skipping entries that cannot be read.
#[must_use]
pub fn events_from(body: &Value) -> Vec<TelemetryEvent> {
    if body["status"] != "success" {
        return Vec::new();
    }
    let Some(results) = body["data"]["result"].as_array() else {
        return Vec::new();
    };
    results.iter().filter_map(event_from).collect()
}

fn event_from(entry: &Value) -> Option<TelemetryEvent> {
    let metric = entry["metric"].as_object()?;
    let name = metric
        .get("__name__")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let pair = entry["value"].as_array()?;
    let epoch = pair.first()?.as_f64()?;
    // Prometheus sends the sample as text, including "NaN" and "+Inf".
    let value: f64 = pair.get(1)?.as_str()?.parse().ok()?;
    let labels = metric
        .iter()
        .filter(|(key, _)| *key != "__name__")
        .map(|(key, value)| Some((key.clone(), value.as_str()?.to_string())))
        .collect::<Option<_>>()?;
    Some(TelemetryEvent {
        fingerprint: fingerprint("prometheus", &name, &labels),
        source: "prometheus".to_string(),
        kind: TelemetryKind::Metric,
        name,
        value: Some(value),
        payload: None,
        labels,
        ts: Timestamp::from_epoch(epoch)?,
    })
}

/// Poll and publish until the task is dropped. A failed publish is logged and
/// the next interval tries again.
pub async fn run(source: Source, bus: Bus, interval: Duration) {
    loop {
        let events = source.poll().await;
        if let Err(error) = bus.publish_all(topics::TELEMETRY_RAW, &events).await {
            tracing::warn!(%error, "could not publish polled telemetry");
        }
        tokio::time::sleep(interval).await;
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn skips_only_the_malformed_entries() {
        let body = json!({"status": "success", "data": {"result": [
            {"metric": {"__name__": "cpu_usage", "service": "a"}, "value": [1767323045.5, "92"]},
            {"metric": {"__name__": "short"}, "value": [1.0]},
            {"metric": {"__name__": "bad"}, "value": [1.0, "x"]},
            {"metric": {}, "value": [1.0, "+Inf"]},
        ]}});
        let events = events_from(&body);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].name, "cpu_usage");
        assert_eq!(events[0].value, Some(92.0));
        assert_eq!(events[0].ts.to_string(), "2026-01-02T03:04:05.500000Z");
        assert!(!events[0].labels.contains_key("__name__"));
        assert_eq!(events[1].name, "unknown");
    }

    #[test]
    fn a_failed_query_yields_nothing() {
        assert!(events_from(&json!({"status": "error"})).is_empty());
        assert!(events_from(&json!([1])).is_empty());
    }
}
