//! The audit trail and the labelled outcomes. Both are append-only, and a
//! failed write is an error the caller must see.

use intelliops_contracts::{AuditRecord, TrainingRecord};
use sqlx::types::Json;

use crate::{Result, Store, lock};

fn column<T: serde::Serialize>(value: &T) -> Result<String> {
    Ok(serde_json::to_value(value)?
        .as_str()
        .unwrap_or_default()
        .to_string())
}

impl Store {
    pub async fn audit_write(&self, record: &AuditRecord) -> Result<()> {
        match self {
            Self::Memory(memory) => lock(memory).audit.push(record.clone()),
            Self::Postgres(pool) => {
                sqlx::query(
                    "INSERT INTO audit_records \
                     (correlation_id, actor, action, resource, decision, ts, payload) \
                     VALUES ($1, $2, $3, $4, $5, $6, $7)",
                )
                .bind(&record.correlation_id)
                .bind(&record.actor)
                .bind(&record.action)
                .bind(&record.resource)
                .bind(&record.decision)
                .bind(record.ts.0)
                .bind(Json(record))
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    /// Oldest first, optionally for one correlation id.
    pub async fn audit_records(&self, correlation_id: Option<&str>) -> Result<Vec<AuditRecord>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory)
                .audit
                .iter()
                .filter(|r| correlation_id.is_none_or(|id| r.correlation_id == id))
                .cloned()
                .collect()),
            Self::Postgres(pool) => {
                let rows: Vec<Json<AuditRecord>> = sqlx::query_scalar(
                    "SELECT payload FROM audit_records \
                     WHERE $1::text IS NULL OR correlation_id = $1 ORDER BY id",
                )
                .bind(correlation_id)
                .fetch_all(pool)
                .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }

    pub async fn training_append(&self, record: &TrainingRecord) -> Result<()> {
        match self {
            Self::Memory(memory) => lock(memory).training.push(record.clone()),
            Self::Postgres(pool) => {
                sqlx::query(
                    "INSERT INTO training_records \
                     (situation_id, signature, playbook_id, result, worked, ts, payload) \
                     VALUES ($1, $2, $3, $4, $5, $6, $7)",
                )
                .bind(&record.situation_id)
                .bind(&record.signature)
                .bind(&record.playbook_id)
                .bind(column(&record.result)?)
                .bind(record.worked)
                .bind(record.ts.0)
                .bind(Json(record))
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    pub async fn training_all(&self) -> Result<Vec<TrainingRecord>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).training.clone()),
            Self::Postgres(pool) => {
                let rows: Vec<Json<TrainingRecord>> =
                    sqlx::query_scalar("SELECT payload FROM training_records ORDER BY id")
                        .fetch_all(pool)
                        .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }
}
