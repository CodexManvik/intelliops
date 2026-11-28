//! What read asks others for: each service's own posture, rca's LLM state and
//! Prometheus history. Every call fails soft.

use std::collections::HashMap;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use intelliops_llm::redact;
use intelliops_service::Config;
use serde_json::{Map, Value, json};
use tokio::sync::Mutex;

const PEER_TIMEOUT: Duration = Duration::from_secs(2);
const PROMETHEUS_TIMEOUT: Duration = Duration::from_secs(5);
const CACHE_FOR: Duration = Duration::from_secs(5);

/// Which service owns each posture field. Read answering from its own
/// environment would report another process's configuration as fact.
const OWNERS: [(&str, &str, &str); 8] = [
    ("correlator_kind", "correlation", "river"),
    ("detection_policy", "correlation", "off"),
    ("correlation_group_by", "correlation", "window"),
    ("remediator_mode", "action", "dry_run"),
    ("health_check_mode", "action", "always"),
    ("sandbox_mode", "action", "off"),
    ("store_backend", "governance", "file"),
    ("runbook_selector_mode", "rca", "off"),
];

pub struct Peers {
    client: reqwest::Client,
    config: Config,
    cache: Mutex<Option<(Instant, Value)>>,
}

impl Peers {
    pub fn new(config: Config) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: reqwest::Client::builder().build()?,
            config,
            cache: Mutex::new(None),
        })
    }

    async fn get(&self, url: String) -> Option<Value> {
        let mut request = self.client.get(url).timeout(PEER_TIMEOUT);
        let token = self.config.get("auth_token", "");
        if self.config.get("auth_mode", "off") == "token" && !token.is_empty() {
            request = request.bearer_auth(token);
        }
        match request.send().await.and_then(|r| r.error_for_status()) {
            Ok(response) => response.json().await.ok(),
            Err(error) => {
                tracing::debug!(%error, "peer unavailable");
                None
            }
        }
    }

    /// Every service listens on one port behind its own name, so a peer's URL
    /// follows from governance's.
    fn url_of(&self, service: &str) -> Option<String> {
        let governance = self.config.get("governance_url", "http://localhost:8005");
        match service {
            "rca" => Some(self.config.get("rca_url", "http://localhost:8003")),
            "governance" => Some(governance),
            _ => {
                let url = reqwest::Url::parse(&governance).ok()?;
                let port = url.port_or_known_default()?;
                Some(format!("{}://{service}:{port}", url.scheme()))
            }
        }
    }

    async fn posture(&self) -> Map<String, Value> {
        let mut fetched: HashMap<&str, Option<Value>> = HashMap::new();
        let mut out = Map::new();
        let mut unverified = Vec::new();
        for (field, owner, default) in OWNERS {
            if !fetched.contains_key(owner) {
                let body = match self.url_of(owner) {
                    Some(url) => self.get(format!("{url}/config/posture")).await,
                    None => None,
                };
                fetched.insert(owner, body);
            }
            let value = fetched
                .get(owner)
                .and_then(Option::as_ref)
                .and_then(|body| body.get(field));
            match value {
                Some(value) => {
                    out.insert(field.to_string(), value.clone());
                }
                // Say it is unverified rather than pass ours off as theirs.
                None => {
                    out.insert(field.to_string(), self.config.get(field, default).into());
                    unverified.push(field);
                }
            }
        }
        if !unverified.is_empty() {
            out.insert("_unverified".to_string(), json!(unverified));
        }
        out
    }

    async fn llm(&self) -> Value {
        let url = self.config.get("rca_url", "http://localhost:8003");
        if let Some(body) = self.get(format!("{url}/config/llm")).await {
            let text = |key: &str, default: &str| {
                body.get(key)
                    .and_then(Value::as_str)
                    .unwrap_or(default)
                    .to_string()
            };
            return json!({
                "provider": text("provider", "template"),
                "endpoint_configured": body["endpoint_configured"] == true,
                "endpoint": text("endpoint", ""),
                "model": text("model", ""),
                "last_probe": body["last_probe"],
                "source": "rca",
            });
        }
        let endpoint = self.config.get("llm_explanation_endpoint", "");
        json!({
            "provider": if endpoint.is_empty() { "template" } else { "openai-compatible" },
            "endpoint_configured": !endpoint.is_empty(),
            "endpoint": redact(&endpoint),
            "model": self.config.get("llm_explanation_model", "gpt-4o-mini"),
            "last_probe": null,
            "source": "unavailable",
        })
    }

    /// The aggregated posture, cached briefly because the console polls it.
    pub async fn system(&self) -> Value {
        let mut cache = self.cache.lock().await;
        if let Some((at, value)) = &*cache
            && at.elapsed() < CACHE_FOR
        {
            return value.clone();
        }
        let mut body = Map::new();
        // Read does own these two: it is the process answering.
        body.insert(
            "bus_backend".into(),
            self.config.get("bus_backend", "redis").into(),
        );
        body.insert(
            "auth_mode".into(),
            self.config.get("auth_mode", "off").into(),
        );
        body.extend(self.posture().await);
        body.insert("llm".into(), self.llm().await);
        let value = Value::Object(body);
        *cache = Some((Instant::now(), value.clone()));
        value
    }

    /// A Prometheus range query. Unreachable gives `available: false`, so the
    /// console says "no data" instead of drawing something.
    pub async fn history(
        &self,
        metric: &str,
        minutes: f64,
        step_seconds: f64,
        service: Option<&str>,
    ) -> Value {
        let minutes = minutes.clamp(1.0, 360.0);
        let step = step_seconds.clamp(5.0, 300.0);
        let end = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0.0, |d| d.as_secs_f64());
        let start = end - minutes * 60.0;
        let query = service.map_or_else(
            || metric.to_string(),
            |service| format!("{metric}{{service=\"{service}\"}}"),
        );
        let url = format!(
            "{}/api/v1/query_range",
            self.config.get("prometheus_url", "http://localhost:9090")
        );
        let request = self.client.get(url).timeout(PROMETHEUS_TIMEOUT).query(&[
            ("query", query),
            ("start", start.to_string()),
            ("end", end.to_string()),
            ("step", step.to_string()),
        ]);
        let series = match request.send().await.and_then(|r| r.error_for_status()) {
            Ok(response) => response.json::<Value>().await.ok().and_then(|b| series(&b)),
            Err(error) => {
                tracing::info!(%error, metric, "metrics history unavailable");
                None
            }
        };
        let mut body = json!({
            "metric": metric,
            "available": series.is_some(),
            "start": start,
            "end": end,
            "step_seconds": step,
            "series": series.clone().unwrap_or_default(),
        });
        if series.is_none() {
            body["reason"] = "prometheus unreachable".into();
        }
        body
    }
}

/// Numbers rather than Prometheus's strings. `None` for an unusable body.
fn series(body: &Value) -> Option<Vec<Value>> {
    if body["status"] != "success" {
        return None;
    }
    body["data"]["result"]
        .as_array()?
        .iter()
        .map(|result| {
            let points = result["values"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or_default()
                .iter()
                .map(|pair| {
                    let value: f64 = pair[1].as_str()?.parse().ok()?;
                    Some(json!([pair[0].as_f64()?, value]))
                })
                .collect::<Option<Vec<_>>>()?;
            let service = result["metric"]["service"].as_str().unwrap_or("unknown");
            Some(json!({"service": service, "points": points}))
        })
        .collect()
}
