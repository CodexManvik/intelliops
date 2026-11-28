//! Governance over HTTP. Approvals live in its database, so a human can decide
//! from the console.

use std::time::Duration;

use intelliops_contracts::{ApprovalRequest, AuditRecord};
use serde_json::{Value, json};
use tokio::time::Instant;

use crate::gates::{ACTOR, Gate};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(5);
const AUDIT_ATTEMPTS: u32 = 5;

pub struct HttpGate {
    client: reqwest::Client,
    url: String,
    token: Option<String>,
    wait: Duration,
    poll: Duration,
}

impl HttpGate {
    pub fn new(
        url: &str,
        token: Option<String>,
        wait: Duration,
        poll: Duration,
    ) -> Result<Self, reqwest::Error> {
        Ok(Self {
            client: reqwest::Client::builder()
                .timeout(REQUEST_TIMEOUT)
                .build()?,
            url: url.trim_end_matches('/').to_string(),
            token,
            wait,
            poll: poll.max(Duration::from_millis(50)),
        })
    }

    fn authorize(&self, request: reqwest::RequestBuilder) -> reqwest::RequestBuilder {
        match &self.token {
            Some(token) => request.bearer_auth(token),
            None => request,
        }
    }

    async fn post(&self, path: &str, body: &Value) -> Result<Value, reqwest::Error> {
        let request = self.client.post(format!("{}{path}", self.url)).json(body);
        self.authorize(request)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await
    }

    async fn approval(&self, id: &str) -> Result<ApprovalRequest, reqwest::Error> {
        let request = self.client.get(format!("{}/approvals/{id}", self.url));
        self.authorize(request)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await
    }
}

impl Gate for HttpGate {
    async fn allowed(&self, resource: &str) -> bool {
        let question = json!({"actor": ACTOR, "action": "execute", "resource": resource});
        match self.post("/rbac/check", &question).await {
            Ok(answer) => answer["allowed"] == true,
            Err(error) => {
                tracing::warn!(%error, "permission check failed; denying");
                false
            }
        }
    }

    async fn request_approval(&self, request: &ApprovalRequest) {
        // A failure leaves nothing to approve, so the wait times out.
        if let Err(error) = self.post("/approvals", &json!(request)).await {
            tracing::warn!(%error, "could not request approval");
        }
    }

    async fn await_decision(&self, approval_id: &str) -> String {
        let deadline = Instant::now() + self.wait;
        loop {
            match self.approval(approval_id).await {
                Ok(request) if request.status != "pending" => return request.status,
                Ok(_) => {}
                Err(error) => tracing::debug!(%error, "approval poll failed"),
            }
            if Instant::now() >= deadline {
                return "pending".to_string();
            }
            tokio::time::sleep(self.poll).await;
        }
    }

    async fn audit(&self, record: &AuditRecord) {
        let body = json!(record);
        for attempt in 1..=AUDIT_ATTEMPTS {
            match self.post("/audit", &body).await {
                Ok(_) => return,
                Err(error) if attempt < AUDIT_ATTEMPTS => {
                    tracing::warn!(%error, attempt, "audit write failed; retrying");
                    tokio::time::sleep(Duration::from_secs(u64::from(attempt))).await;
                }
                Err(error) => {
                    // Last resort: the record at least reaches the logs.
                    tracing::error!(%error, record = %body, "audit record could not be written");
                }
            }
        }
    }

    async fn expire(&self, approval_id: &str) {
        let path = format!("/approvals/{approval_id}/expire");
        if let Err(error) = self.post(&path, &json!({"actor": ACTOR})).await {
            tracing::debug!(%error, "could not expire the approval");
        }
    }
}
