//! The runbook author's memory of its decisions and the trace of its runs.

use intelliops_contracts::{
    AuthorDecision, DecisionOutcome, Disposition, RunSummary, Timestamp, TraceStep, TraceStepKind,
};
use sqlx::types::Json;

use crate::{Result, Store, lock};

fn name<T: serde::Serialize>(value: &T) -> Result<String> {
    Ok(serde_json::to_value(value)?
        .as_str()
        .unwrap_or_default()
        .to_string())
}

impl Store {
    pub async fn decision_record(&self, decision: &AuthorDecision) -> Result<()> {
        match self {
            Self::Memory(memory) => lock(memory).decisions.push(decision.clone()),
            Self::Postgres(pool) => {
                sqlx::query(
                    "INSERT INTO author_decisions \
                     (signature, proposal_id, playbook_id, disposition, outcome, ts, payload) \
                     VALUES ($1, $2, $3, $4, $5, $6, $7)",
                )
                .bind(&decision.signature)
                .bind(&decision.proposal_id)
                .bind(&decision.playbook_id)
                .bind(name(&decision.disposition)?)
                .bind(name(&decision.outcome)?)
                .bind(decision.ts.0)
                .bind(Json(decision))
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    pub async fn decisions_by_signature(&self, signature: &str) -> Result<Vec<AuthorDecision>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory)
                .decisions
                .iter()
                .filter(|d| d.signature == signature)
                .cloned()
                .collect()),
            Self::Postgres(pool) => {
                let rows: Vec<Json<AuthorDecision>> = sqlx::query_scalar(
                    "SELECT payload FROM author_decisions WHERE signature = $1 ORDER BY id",
                )
                .bind(signature)
                .fetch_all(pool)
                .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }

    /// A no-op when nothing matches.
    pub async fn decision_disposition(
        &self,
        proposal_id: &str,
        disposition: Disposition,
        decided_by: &str,
    ) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                for d in lock(memory).decisions.iter_mut() {
                    if d.proposal_id == proposal_id {
                        d.disposition = disposition;
                        d.decided_by = Some(decided_by.to_string());
                    }
                }
            }
            Self::Postgres(pool) => {
                sqlx::query(
                    "UPDATE author_decisions SET disposition = $2, payload = payload || \
                     jsonb_build_object('disposition', $2::text, 'decided_by', $3::text) \
                     WHERE proposal_id = $1",
                )
                .bind(proposal_id)
                .bind(name(&disposition)?)
                .bind(decided_by)
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    pub async fn decision_outcome(
        &self,
        playbook_id: &str,
        outcome: DecisionOutcome,
    ) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                for d in lock(memory).decisions.iter_mut() {
                    if d.playbook_id == playbook_id {
                        d.outcome = outcome;
                    }
                }
            }
            Self::Postgres(pool) => {
                sqlx::query(
                    "UPDATE author_decisions SET outcome = $2, payload = payload || \
                     jsonb_build_object('outcome', $2::text) WHERE playbook_id = $1",
                )
                .bind(playbook_id)
                .bind(name(&outcome)?)
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    pub async fn run_start(&self, run_id: &str, signature: &str, at: Timestamp) -> Result<()> {
        let summary = RunSummary {
            run_id: run_id.to_string(),
            started_at: at,
            status: "running".to_string(),
            signature: signature.to_string(),
            step_count: 0,
            proposal_id: None,
        };
        match self {
            Self::Memory(memory) => lock(memory).runs.push(summary),
            Self::Postgres(pool) => {
                sqlx::query(
                    "INSERT INTO agent_runs \
                     (run_id, signature, status, started_at, step_count, payload) \
                     VALUES ($1, $2, 'running', $3, 0, $4)",
                )
                .bind(run_id)
                .bind(signature)
                .bind(at.0)
                .bind(Json(&summary))
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    /// Append a step and bump the run's counter together.
    pub async fn run_step(&self, step: &TraceStep) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                let Some(run) = memory.runs.iter_mut().find(|r| r.run_id == step.run_id) else {
                    return Ok(());
                };
                run.step_count += 1;
                memory.steps.push(step.clone());
            }
            Self::Postgres(pool) => {
                let kind: &str = match step.kind {
                    TraceStepKind::ModelTurn => "model_turn",
                    TraceStepKind::ToolCall => "tool_call",
                    TraceStepKind::Submit => "submit",
                    TraceStepKind::Outcome => "outcome",
                };
                let mut transaction = pool.begin().await?;
                sqlx::query(
                    "INSERT INTO agent_run_steps (run_id, seq, kind, ts, payload) \
                     VALUES ($1, $2, $3, $4, $5)",
                )
                .bind(&step.run_id)
                .bind(i32::try_from(step.seq).unwrap_or(i32::MAX))
                .bind(kind)
                .bind(step.ts.0)
                .bind(Json(step))
                .execute(&mut *transaction)
                .await?;
                sqlx::query(
                    "UPDATE agent_runs SET step_count = step_count + 1, payload = \
                     jsonb_set(payload, '{step_count}', to_jsonb(step_count + 1)) \
                     WHERE run_id = $1",
                )
                .bind(&step.run_id)
                .execute(&mut *transaction)
                .await?;
                transaction.commit().await?;
            }
        }
        Ok(())
    }

    pub async fn run_finish(
        &self,
        run_id: &str,
        status: &str,
        proposal_id: Option<&str>,
        at: Timestamp,
    ) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                for run in lock(memory).runs.iter_mut() {
                    if run.run_id == run_id {
                        run.status = status.to_string();
                        run.proposal_id = proposal_id.map(str::to_string);
                    }
                }
            }
            Self::Postgres(pool) => {
                sqlx::query(
                    "UPDATE agent_runs SET status = $2, proposal_id = $3, finished_at = $4, \
                     payload = payload || jsonb_build_object('status', $2::text, \
                     'proposal_id', $3::text) WHERE run_id = $1",
                )
                .bind(run_id)
                .bind(status)
                .bind(proposal_id)
                .bind(at.0)
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    /// In `seq` order.
    pub async fn run_steps(&self, run_id: &str) -> Result<Vec<TraceStep>> {
        match self {
            Self::Memory(memory) => {
                let mut steps: Vec<TraceStep> = lock(memory)
                    .steps
                    .iter()
                    .filter(|s| s.run_id == run_id)
                    .cloned()
                    .collect();
                steps.sort_by_key(|s| s.seq);
                Ok(steps)
            }
            Self::Postgres(pool) => {
                let rows: Vec<Json<TraceStep>> = sqlx::query_scalar(
                    "SELECT payload FROM agent_run_steps WHERE run_id = $1 ORDER BY seq",
                )
                .bind(run_id)
                .fetch_all(pool)
                .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }

    /// Newest first.
    pub async fn recent_runs(&self, limit: i64) -> Result<Vec<RunSummary>> {
        match self {
            Self::Memory(memory) => {
                let mut runs = lock(memory).runs.clone();
                runs.sort_by_key(|r| std::cmp::Reverse(r.started_at));
                runs.truncate(usize::try_from(limit).unwrap_or(0));
                Ok(runs)
            }
            Self::Postgres(pool) => {
                let rows: Vec<Json<RunSummary>> = sqlx::query_scalar(
                    "SELECT payload FROM agent_runs ORDER BY started_at DESC LIMIT $1",
                )
                .bind(limit)
                .fetch_all(pool)
                .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }
}
