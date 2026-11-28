//! Pending approvals and AI runbook proposals.

use intelliops_contracts::{ApprovalRequest, ProposalStatus, ProposedPlaybook};
use sqlx::types::Json;

use crate::{Result, Store, lock};

fn status_name(status: ProposalStatus) -> &'static str {
    match status {
        ProposalStatus::Proposed => "proposed",
        ProposalStatus::Approved => "approved",
        ProposalStatus::Rejected => "rejected",
    }
}

impl Store {
    /// Create, or replace the request with the same id.
    pub async fn approval_put(&self, request: &ApprovalRequest) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                memory.approvals.retain(|a| a.id != request.id);
                memory.approvals.push(request.clone());
            }
            Self::Postgres(pool) => {
                sqlx::query(
                    "INSERT INTO approvals \
                     (id, situation_id, playbook_id, status, payload, updated_at) \
                     VALUES ($1, $2, $3, $4, $5, now()) ON CONFLICT (id) DO UPDATE SET \
                     situation_id = excluded.situation_id, playbook_id = excluded.playbook_id, \
                     status = excluded.status, payload = excluded.payload, \
                     updated_at = excluded.updated_at",
                )
                .bind(&request.id)
                .bind(&request.situation_id)
                .bind(&request.playbook_id)
                .bind(&request.status)
                .bind(Json(request))
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    pub async fn approval_get(&self, id: &str) -> Result<Option<ApprovalRequest>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).approvals.iter().find(|a| a.id == id).cloned()),
            Self::Postgres(pool) => {
                let row: Option<Json<ApprovalRequest>> =
                    sqlx::query_scalar("SELECT payload FROM approvals WHERE id = $1")
                        .bind(id)
                        .fetch_optional(pool)
                        .await?;
                Ok(row.map(|row| row.0))
            }
        }
    }

    /// Move a request out of `pending`. `None` if it is missing or already
    /// decided, so two deciders cannot both win (ADR-010).
    pub async fn approval_decide(
        &self,
        id: &str,
        status: &str,
        decided_by: &str,
    ) -> Result<Option<ApprovalRequest>> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                let Some(found) = memory
                    .approvals
                    .iter_mut()
                    .find(|a| a.id == id && a.status == "pending")
                else {
                    return Ok(None);
                };
                found.status = status.to_string();
                found.decided_by = Some(decided_by.to_string());
                Ok(Some(found.clone()))
            }
            Self::Postgres(pool) => {
                let row: Option<Json<ApprovalRequest>> = sqlx::query_scalar(
                    "UPDATE approvals SET status = $2, updated_at = now(), \
                     payload = payload || jsonb_build_object('status', $2::text, \
                     'decided_by', $3::text) \
                     WHERE id = $1 AND status = 'pending' RETURNING payload",
                )
                .bind(id)
                .bind(status)
                .bind(decided_by)
                .fetch_optional(pool)
                .await?;
                Ok(row.map(|row| row.0))
            }
        }
    }

    pub async fn approvals_pending(&self) -> Result<Vec<ApprovalRequest>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory)
                .approvals
                .iter()
                .filter(|a| a.status == "pending")
                .cloned()
                .collect()),
            Self::Postgres(pool) => {
                let rows: Vec<Json<ApprovalRequest>> = sqlx::query_scalar(
                    "SELECT payload FROM approvals WHERE status = 'pending' ORDER BY updated_at",
                )
                .fetch_all(pool)
                .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }

    pub async fn approvals_clear(&self) -> Result<()> {
        match self {
            Self::Memory(memory) => lock(memory).approvals.clear(),
            Self::Postgres(pool) => {
                sqlx::query("DELETE FROM approvals").execute(pool).await?;
            }
        }
        Ok(())
    }

    pub async fn proposal_add(&self, proposal: &ProposedPlaybook) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                memory.proposals.retain(|p| p.id != proposal.id);
                memory.proposals.push(proposal.clone());
            }
            Self::Postgres(pool) => {
                let mut transaction = pool.begin().await?;
                sqlx::query("DELETE FROM proposed_playbooks WHERE proposal_id = $1")
                    .bind(&proposal.id)
                    .execute(&mut *transaction)
                    .await?;
                sqlx::query(
                    "INSERT INTO proposed_playbooks \
                     (proposal_id, status, source_situation_id, ts, payload) \
                     VALUES ($1, $2, $3, $4, $5)",
                )
                .bind(&proposal.id)
                .bind(status_name(proposal.status))
                .bind(&proposal.source_situation_id)
                .bind(proposal.ts.0)
                .bind(Json(proposal))
                .execute(&mut *transaction)
                .await?;
                transaction.commit().await?;
            }
        }
        Ok(())
    }

    pub async fn proposal_get(&self, id: &str) -> Result<Option<ProposedPlaybook>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).proposals.iter().find(|p| p.id == id).cloned()),
            Self::Postgres(pool) => {
                let row: Option<Json<ProposedPlaybook>> = sqlx::query_scalar(
                    "SELECT payload FROM proposed_playbooks WHERE proposal_id = $1",
                )
                .bind(id)
                .fetch_optional(pool)
                .await?;
                Ok(row.map(|row| row.0))
            }
        }
    }

    pub async fn proposals(&self, status: Option<ProposalStatus>) -> Result<Vec<ProposedPlaybook>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory)
                .proposals
                .iter()
                .filter(|p| status.is_none_or(|s| p.status == s))
                .cloned()
                .collect()),
            Self::Postgres(pool) => {
                let rows: Vec<Json<ProposedPlaybook>> = sqlx::query_scalar(
                    "SELECT payload FROM proposed_playbooks \
                     WHERE $1::text IS NULL OR status = $1 ORDER BY id",
                )
                .bind(status.map(status_name))
                .fetch_all(pool)
                .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }

    /// Decide a proposal that is still open. `None` if missing or decided.
    pub async fn proposal_decide(
        &self,
        id: &str,
        status: ProposalStatus,
        decided_by: &str,
    ) -> Result<Option<ProposedPlaybook>> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                let Some(found) = memory
                    .proposals
                    .iter_mut()
                    .find(|p| p.id == id && p.status == ProposalStatus::Proposed)
                else {
                    return Ok(None);
                };
                found.status = status;
                found.decided_by = Some(decided_by.to_string());
                Ok(Some(found.clone()))
            }
            Self::Postgres(pool) => {
                let row: Option<Json<ProposedPlaybook>> = sqlx::query_scalar(
                    "UPDATE proposed_playbooks SET status = $2, \
                     payload = payload || jsonb_build_object('status', $2::text, \
                     'decided_by', $3::text) \
                     WHERE proposal_id = $1 AND status = 'proposed' RETURNING payload",
                )
                .bind(id)
                .bind(status_name(status))
                .bind(decided_by)
                .fetch_optional(pool)
                .await?;
                Ok(row.map(|row| row.0))
            }
        }
    }

    pub async fn proposals_clear(&self) -> Result<()> {
        match self {
            Self::Memory(memory) => lock(memory).proposals.clear(),
            Self::Postgres(pool) => {
                sqlx::query("DELETE FROM proposed_playbooks")
                    .execute(pool)
                    .await?;
            }
        }
        Ok(())
    }
}
