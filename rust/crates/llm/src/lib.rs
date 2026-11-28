//! The only way the platform talks to a language model. It never panics and
//! never retries what will not heal: a 429 is waited out, anything else ends.

use std::time::Duration;

use serde_json::Value;

const DEFAULT_BACKOFF: Duration = Duration::from_secs(5);
const MAX_BACKOFF: Duration = Duration::from_secs(15);
const LOGGED_BODY_CHARS: usize = 400;

#[derive(Debug, Clone)]
pub struct ChatClient {
    client: reqwest::Client,
    base_url: String,
    api_key: String,
    pub model: String,
    max_attempts: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatError {
    /// Still rate limited after the whole attempt budget.
    RateLimited,
    /// Unreachable, an error status, or a reply without a message.
    Failed,
}

/// The `Retry-After` header, or "try again in 1m2.5s" from the body.
fn retry_after(header: Option<&str>, body: &str) -> Option<Duration> {
    if let Some(seconds) = header.and_then(|h| h.trim().parse::<f64>().ok()) {
        return Duration::try_from_secs_f64(seconds).ok();
    }
    let lower = body.to_ascii_lowercase();
    let rest = lower.split_once("try again in ")?.1;
    let end = rest.find('s')?;
    let spec = rest.get(..end)?;
    let (minutes, seconds) = match spec.split_once('m') {
        Some((m, s)) => (m.parse::<f64>().ok()?, s),
        None => (0.0, spec),
    };
    Duration::try_from_secs_f64(minutes * 60.0 + seconds.parse::<f64>().ok()?).ok()
}

impl ChatClient {
    pub fn new(
        base_url: &str,
        model: &str,
        api_key: &str,
        timeout: Duration,
    ) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: reqwest::Client::builder().timeout(timeout).build()?,
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.to_string(),
            model: model.to_string(),
            max_attempts: 3,
        })
    }

    #[must_use]
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// POST `payload` and return `choices[0].message`.
    pub async fn message(&self, payload: &Value) -> Result<Value, ChatError> {
        for attempt in 1..=self.max_attempts {
            let mut request = self
                .client
                .post(format!("{}/chat/completions", self.base_url))
                .json(payload);
            if !self.api_key.is_empty() {
                request = request.bearer_auth(&self.api_key);
            }
            let response = match request.send().await {
                Ok(response) => response,
                Err(error) => {
                    tracing::info!(error = %error.without_url(), "model endpoint unreachable");
                    return Err(ChatError::Failed);
                }
            };
            let status = response.status();
            if status.as_u16() == 429 {
                if attempt == self.max_attempts {
                    return Err(ChatError::RateLimited);
                }
                let header = response
                    .headers()
                    .get("retry-after")
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_string);
                let body = response.text().await.unwrap_or_default();
                let delay = retry_after(header.as_deref(), &body)
                    .unwrap_or(DEFAULT_BACKOFF)
                    .min(MAX_BACKOFF);
                tracing::info!(?delay, "model rate limited; backing off");
                tokio::time::sleep(delay).await;
                continue;
            }
            if !status.is_success() {
                // Without the body a 4xx cannot be diagnosed. The key travels
                // in the request header, never back in the response.
                let body = response.text().await.unwrap_or_default();
                let detail: String = body.chars().take(LOGGED_BODY_CHARS).collect();
                tracing::info!(%status, detail, "model endpoint refused");
                return Err(ChatError::Failed);
            }
            let body: Value = response.json().await.map_err(|_| ChatError::Failed)?;
            return match body.pointer("/choices/0/message") {
                Some(message @ Value::Object(_)) => Ok(message.clone()),
                _ => Err(ChatError::Failed),
            };
        }
        Err(ChatError::RateLimited)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_advised_delay() {
        let secs = |h, b| retry_after(h, b).map(|d| d.as_secs_f64());
        assert_eq!(secs(Some("2"), ""), Some(2.0));
        assert_eq!(secs(None, "Please try again in 6.51s."), Some(6.51));
        assert_eq!(secs(None, "try again in 1m2.5s"), Some(62.5));
        assert_eq!(secs(None, "slow down"), None);
    }
}
