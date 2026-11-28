//! The governance HTTP API. Every state change that matters is audited.

use std::sync::Arc;

use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use intelliops_contracts::{
    ApprovalRequest, AuditRecord, AuthorDecision, DecisionOutcome, Disposition, HitlMode, Playbook,
    ProposalStatus, ProposedPlaybook, Timestamp,
};
use intelliops_store::{Store, StoreError};
use serde::Deserialize;
use serde_json::json;

use crate::author::{Author, DraftSituation, NoTrace, Toolbox};
use crate::context::SystemContext;
use crate::drafting::{Hub, draft_in_background, stream_run};
use crate::rbac::Policy;

#[derive(Clone)]
pub struct Governance {
    pub store: Store,
    pub policy: Arc<Policy>,
    /// `None` when AI drafting is off.
    pub author: Option<Arc<Author>>,
    pub context: SystemContext,
    pub hub: Hub,
    /// Set in token auth mode, for the stream that authenticates itself.
    pub stream_token: Option<String>,
}

pub struct ApiError(pub StatusCode, pub String);

impl ApiError {
    fn new(status: StatusCode, detail: impl Into<String>) -> Self {
        Self(status, detail.into())
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({"detail": self.1}))).into_response()
    }
}

impl From<StoreError> for ApiError {
    fn from(error: StoreError) -> Self {
        tracing::error!(%error, "storage failed");
        Self::new(StatusCode::SERVICE_UNAVAILABLE, "database unavailable")
    }
}

type Reply<T> = Result<Json<T>, ApiError>;

fn not_found(what: &str) -> ApiError {
    ApiError::new(StatusCode::NOT_FOUND, format!("{what} not found"))
}

fn forbidden(detail: &str) -> ApiError {
    ApiError::new(StatusCode::FORBIDDEN, detail)
}

fn hex(length: usize) -> String {
    let mut id = uuid::Uuid::new_v4().simple().to_string();
    id.truncate(length);
    id
}

impl Governance {
    fn require(
        &self,
        actor: &str,
        action: &str,
        resource: &str,
        why: &str,
    ) -> Result<(), ApiError> {
        if self.policy.check(actor, action, resource) {
            Ok(())
        } else {
            Err(forbidden(why))
        }
    }

    async fn audit(
        &self,
        actor: &str,
        action: &str,
        resource: &str,
        correlation_id: &str,
    ) -> Result<(), StoreError> {
        self.store
            .audit_write(&AuditRecord {
                actor: actor.to_string(),
                action: action.to_string(),
                resource: resource.to_string(),
                decision: "allow".to_string(),
                ts: Timestamp::now(),
                correlation_id: correlation_id.to_string(),
            })
            .await
    }

    /// Store and audit a drafted runbook. It is always forced to HITL with a
    /// server-assigned id: the model never chooses either.
    pub(crate) async fn finalize(
        &self,
        situation: &DraftSituation,
        draft: crate::author::Draft,
        requested_by: &str,
    ) -> Result<ProposedPlaybook, StoreError> {
        let mut playbook = draft.playbook;
        playbook.hitl_mode = HitlMode::Hitl;
        playbook.id = format!("ai-{}-{}", situation.signature, hex(6));
        let proposal = ProposedPlaybook {
            id: format!("prop-{}", hex(8)),
            playbook,
            status: ProposalStatus::Proposed,
            proposed_by: "runbook-author".to_string(),
            rationale: draft.rationale,
            source_situation_id: Some(situation.id.clone()),
            decided_by: None,
            ts: Timestamp::now(),
        };
        self.store.proposal_add(&proposal).await?;
        let resource = format!("proposal:{}", proposal.id);
        self.audit(requested_by, "propose", &resource, &situation.id)
            .await?;
        let memory = AuthorDecision {
            signature: situation.signature.clone(),
            proposal_id: proposal.id.clone(),
            playbook_id: proposal.playbook.id.clone(),
            actions: proposal
                .playbook
                .steps
                .iter()
                .filter_map(|s| {
                    serde_json::to_value(s.action)
                        .ok()?
                        .as_str()
                        .map(String::from)
                })
                .collect(),
            cited_facts: draft.cited_facts,
            note: None,
            disposition: Disposition::Pending,
            outcome: DecisionOutcome::Unknown,
            decided_by: None,
            ts: Timestamp::now(),
        };
        // The author's own memory: losing it must not lose the proposal.
        if let Err(error) = self.store.decision_record(&memory).await {
            tracing::warn!(%error, "could not record the author's decision");
        }
        Ok(proposal)
    }

    pub fn routes(self) -> Router {
        Router::new()
            .route("/audit", get(query_audit).post(write_audit))
            .route("/playbooks", get(list_playbooks).post(register_playbook))
            .route("/playbooks/proposed", get(list_proposed).post(propose))
            .route("/playbooks/proposed/{id}", get(get_proposed))
            .route("/playbooks/proposed/{id}/approve", post(approve_proposed))
            .route("/playbooks/proposed/{id}/reject", post(reject_proposed))
            .route("/playbooks/draft-async", post(draft_in_background))
            .route("/playbooks/{id}", get(get_playbook))
            .route("/playbooks/{id}/graduate", post(graduate))
            .route("/playbooks/{id}/demote", post(demote))
            .route("/rbac/check", post(rbac_check))
            .route("/approvals", get(list_approvals).post(create_approval))
            .route("/approvals/{id}", get(get_approval))
            .route("/approvals/{id}/decide", post(decide))
            .route("/approvals/{id}/expire", post(expire))
            .route("/reset-approvals", post(reset_approvals))
            .route("/reset-proposed", post(reset_proposed))
            .route("/agent-runs", get(list_runs))
            .route("/agent-runs/{id}", get(get_run))
            .route("/agent-runs/{id}/stream", get(stream_run))
            .with_state(self)
    }
}

async fn write_audit(
    State(g): State<Governance>,
    Json(record): Json<AuditRecord>,
) -> Reply<serde_json::Value> {
    g.store.audit_write(&record).await?;
    Ok(Json(json!({"status": "ok"})))
}

#[derive(Deserialize)]
struct AuditQuery {
    correlation_id: Option<String>,
}

async fn query_audit(
    State(g): State<Governance>,
    Query(query): Query<AuditQuery>,
) -> Reply<Vec<AuditRecord>> {
    Ok(Json(
        g.store
            .audit_records(query.correlation_id.as_deref())
            .await?,
    ))
}

#[derive(Deserialize)]
struct Registration {
    registered_by: String,
}

async fn register_playbook(
    State(g): State<Governance>,
    Query(by): Query<Registration>,
    Json(playbook): Json<Playbook>,
) -> Reply<serde_json::Value> {
    let resource = format!("playbook:{}", playbook.id);
    g.require(
        &by.registered_by,
        "register",
        &resource,
        "actor lacks register permission",
    )?;
    // AUTO is only reachable by someone who could have graduated it anyway.
    if playbook.hitl_mode == HitlMode::Auto {
        g.require(
            &by.registered_by,
            "graduate",
            &resource,
            "only an actor with graduate permission may register auto",
        )?;
    }
    g.store.playbook_register(&playbook).await?;
    g.audit(&by.registered_by, "register", &resource, &resource)
        .await?;
    Ok(Json(json!({"status": "ok"})))
}

async fn list_playbooks(State(g): State<Governance>) -> Reply<Vec<Playbook>> {
    Ok(Json(g.store.playbook_list().await?))
}

async fn get_playbook(State(g): State<Governance>, Path(id): Path<String>) -> Reply<Playbook> {
    let playbook = g.store.playbook_get(&id).await?;
    playbook.map(Json).ok_or_else(|| not_found("playbook"))
}

#[derive(Deserialize)]
struct RbacQuestion {
    actor: String,
    action: String,
    resource: String,
}

async fn rbac_check(
    State(g): State<Governance>,
    Json(q): Json<RbacQuestion>,
) -> Json<serde_json::Value> {
    Json(json!({"allowed": g.policy.check(&q.actor, &q.action, &q.resource)}))
}

async fn create_approval(
    State(g): State<Governance>,
    Json(request): Json<ApprovalRequest>,
) -> Reply<ApprovalRequest> {
    g.store.approval_put(&request).await?;
    Ok(Json(request))
}

async fn list_approvals(State(g): State<Governance>) -> Reply<Vec<ApprovalRequest>> {
    Ok(Json(g.store.approvals_pending().await?))
}

async fn find_approval(g: &Governance, id: &str) -> Result<ApprovalRequest, ApiError> {
    g.store
        .approval_get(id)
        .await?
        .ok_or_else(|| not_found("approval"))
}

async fn get_approval(
    State(g): State<Governance>,
    Path(id): Path<String>,
) -> Reply<ApprovalRequest> {
    Ok(Json(find_approval(&g, &id).await?))
}

#[derive(Deserialize)]
struct Decision {
    decision: String,
    decided_by: String,
}

async fn decide(
    State(g): State<Governance>,
    Path(id): Path<String>,
    Json(body): Json<Decision>,
) -> Reply<ApprovalRequest> {
    let request = find_approval(&g, &id).await?;
    // Only these: anything else used to be stored and read back as a timeout.
    let permission = match body.decision.as_str() {
        "approved" => "approve",
        "rejected" => "reject",
        _ => {
            return Err(ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "decision must be 'approved' or 'rejected'",
            ));
        }
    };
    let resource = format!("playbook:{}", request.playbook_id);
    g.require(
        &body.decided_by,
        permission,
        &resource,
        &format!("decider lacks {permission} permission"),
    )?;
    // Conditional on still being pending: deciding twice would rewrite history.
    let Some(updated) = g
        .store
        .approval_decide(&id, &body.decision, &body.decided_by)
        .await?
    else {
        let status = find_approval(&g, &id).await?.status;
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            format!("approval is already {status}"),
        ));
    };
    g.audit(
        &body.decided_by,
        permission,
        &resource,
        &request.situation_id,
    )
    .await?;
    Ok(Json(updated))
}

#[derive(Deserialize)]
struct Expiry {
    actor: String,
}

/// Mark a request nobody decided as expired. Only its requester may.
async fn expire(
    State(g): State<Governance>,
    Path(id): Path<String>,
    Json(body): Json<Expiry>,
) -> Reply<ApprovalRequest> {
    let request = find_approval(&g, &id).await?;
    if body.actor != request.requested_by {
        return Err(forbidden("only the requester may expire a request"));
    }
    match g.store.approval_decide(&id, "expired", &body.actor).await? {
        Some(updated) => {
            let resource = format!("playbook:{}", request.playbook_id);
            g.audit(&body.actor, "expire", &resource, &request.situation_id)
                .await?;
            Ok(Json(updated))
        }
        // Already decided: report what it is now.
        None => Ok(Json(find_approval(&g, &id).await?)),
    }
}

async fn reset_approvals(State(g): State<Governance>) -> Reply<serde_json::Value> {
    g.store.approvals_clear().await?;
    Ok(Json(json!({"reset": true})))
}

#[derive(Deserialize)]
struct Decider {
    decided_by: String,
}

async fn set_mode(g: &Governance, id: &str, by: &str, mode: HitlMode) -> Reply<Playbook> {
    let mut playbook = g
        .store
        .playbook_get(id)
        .await?
        .ok_or_else(|| not_found("playbook"))?;
    let resource = format!("playbook:{id}");
    g.require(by, "graduate", &resource, "actor lacks graduate permission")?;
    let verb = if mode == HitlMode::Auto {
        "graduate"
    } else if playbook.hitl_mode == HitlMode::Auto {
        "demote"
    } else {
        // Demoting what is not automatic changes nothing.
        return Ok(Json(playbook));
    };
    playbook.hitl_mode = mode;
    g.store.playbook_register(&playbook).await?;
    g.audit(by, verb, &resource, &resource).await?;
    Ok(Json(playbook))
}

async fn graduate(
    State(g): State<Governance>,
    Path(id): Path<String>,
    Json(body): Json<Decider>,
) -> Reply<Playbook> {
    set_mode(&g, &id, &body.decided_by, HitlMode::Auto).await
}

async fn demote(
    State(g): State<Governance>,
    Path(id): Path<String>,
    Json(body): Json<Decider>,
) -> Reply<Playbook> {
    set_mode(&g, &id, &body.decided_by, HitlMode::Hitl).await
}

#[derive(Deserialize)]
struct StatusFilter {
    status: Option<ProposalStatus>,
}

async fn list_proposed(
    State(g): State<Governance>,
    Query(filter): Query<StatusFilter>,
) -> Reply<Vec<ProposedPlaybook>> {
    Ok(Json(g.store.proposals(filter.status).await?))
}

async fn find_proposal(g: &Governance, id: &str) -> Result<ProposedPlaybook, ApiError> {
    g.store
        .proposal_get(id)
        .await?
        .ok_or_else(|| not_found("proposal"))
}

async fn get_proposed(
    State(g): State<Governance>,
    Path(id): Path<String>,
) -> Reply<ProposedPlaybook> {
    Ok(Json(find_proposal(&g, &id).await?))
}

#[derive(Deserialize)]
pub(crate) struct ProposeRequest {
    pub situation: DraftSituation,
    pub hint: Option<String>,
    pub requested_by: String,
}

impl Governance {
    pub(crate) fn may_propose(&self, actor: &str) -> Result<(), ApiError> {
        self.require(actor, "approve", "playbook:*", "requester lacks permission")
    }

    pub(crate) fn toolbox(&self, situation: DraftSituation) -> Toolbox {
        Toolbox {
            situation,
            context: self.context.clone(),
            store: self.store.clone(),
        }
    }
}

async fn propose(
    State(g): State<Governance>,
    Json(body): Json<ProposeRequest>,
) -> Reply<ProposedPlaybook> {
    g.may_propose(&body.requested_by)?;
    let drafted = match &g.author {
        Some(author) => {
            let toolbox = g.toolbox(body.situation.clone());
            author
                .draft::<NoTrace>(&toolbox, body.hint.as_deref(), None)
                .await
        }
        None => None,
    };
    let Some(draft) = drafted else {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "author could not produce a valid runbook",
        ));
    };
    Ok(Json(
        g.finalize(&body.situation, draft, &body.requested_by)
            .await?,
    ))
}

async fn decide_proposal(
    g: &Governance,
    id: &str,
    by: &str,
    status: ProposalStatus,
) -> Reply<ProposedPlaybook> {
    let proposal = find_proposal(g, id).await?;
    let (verb, action, disposition) = match status {
        ProposalStatus::Approved => ("approve", "approve-proposal", Disposition::Accepted),
        _ => ("reject", "reject-proposal", Disposition::Rejected),
    };
    let resource = format!("playbook:{}", proposal.playbook.id);
    g.require(
        by,
        verb,
        &resource,
        &format!("decider lacks {verb} permission"),
    )?;
    // A rejected proposal must never become approvable, nor the reverse.
    let Some(updated) = g.store.proposal_decide(id, status, by).await? else {
        let current = find_proposal(g, id).await?.status;
        let name = serde_json::to_value(current).unwrap_or_default();
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            format!("proposal is already {}", name.as_str().unwrap_or("decided")),
        ));
    };
    if status == ProposalStatus::Approved {
        g.store.playbook_register(&updated.playbook).await?;
    }
    g.audit(by, action, &format!("proposal:{id}"), id).await?;
    if let Err(error) = g.store.decision_disposition(id, disposition, by).await {
        tracing::warn!(%error, "could not record the disposition");
    }
    Ok(Json(updated))
}

async fn approve_proposed(
    State(g): State<Governance>,
    Path(id): Path<String>,
    Json(body): Json<Decider>,
) -> Reply<ProposedPlaybook> {
    decide_proposal(&g, &id, &body.decided_by, ProposalStatus::Approved).await
}

async fn reject_proposed(
    State(g): State<Governance>,
    Path(id): Path<String>,
    Json(body): Json<Decider>,
) -> Reply<ProposedPlaybook> {
    decide_proposal(&g, &id, &body.decided_by, ProposalStatus::Rejected).await
}

async fn reset_proposed(State(g): State<Governance>) -> Reply<serde_json::Value> {
    g.store.proposals_clear().await?;
    Ok(Json(json!({"reset": true})))
}

async fn list_runs(State(g): State<Governance>) -> Json<serde_json::Value> {
    let runs = g.store.recent_runs(50).await.unwrap_or_else(|error| {
        tracing::warn!(%error, "could not list agent runs");
        Vec::new()
    });
    Json(json!({"runs": runs}))
}

async fn get_run(State(g): State<Governance>, Path(id): Path<String>) -> Reply<serde_json::Value> {
    let steps = g.store.run_steps(&id).await?;
    // No steps means either a run that has none yet or no such run.
    if steps.is_empty()
        && !g
            .store
            .recent_runs(1000)
            .await?
            .iter()
            .any(|r| r.run_id == id)
    {
        return Err(not_found("run"));
    }
    Ok(Json(json!({"run_id": id, "steps": steps})))
}
