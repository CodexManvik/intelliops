//! The detector's persisted baseline and model artifacts. Losing a snapshot is
//! recoverable, so callers log and carry on.

use serde::{Deserialize, Serialize};

use crate::{Result, Store, lock};

/// A row of `correlation_baseline`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, sqlx::FromRow)]
pub struct BaselineRow {
    pub metric_name: String,
    pub n: f64,
    pub mean: f64,
    pub variance: f64,
    pub count: i64,
}

impl Store {
    pub async fn baseline_save(&self, rows: &[BaselineRow]) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                for row in rows {
                    memory.baseline.insert(row.metric_name.clone(), row.clone());
                }
            }
            Self::Postgres(pool) => {
                let mut transaction = pool.begin().await?;
                for row in rows {
                    sqlx::query(
                        "INSERT INTO correlation_baseline \
                         (metric_name, n, mean, variance, count, updated_at) \
                         VALUES ($1, $2, $3, $4, $5, now()) \
                         ON CONFLICT (metric_name) DO UPDATE SET n = excluded.n, \
                         mean = excluded.mean, variance = excluded.variance, \
                         count = excluded.count, updated_at = excluded.updated_at",
                    )
                    .bind(&row.metric_name)
                    .bind(row.n)
                    .bind(row.mean)
                    .bind(row.variance)
                    .bind(row.count)
                    .execute(&mut *transaction)
                    .await?;
                }
                transaction.commit().await?;
            }
        }
        Ok(())
    }

    pub async fn baseline_load(&self) -> Result<Vec<BaselineRow>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).baseline.values().cloned().collect()),
            Self::Postgres(pool) => Ok(sqlx::query_as(
                "SELECT metric_name, n, mean, variance, count FROM correlation_baseline",
            )
            .fetch_all(pool)
            .await?),
        }
    }

    pub async fn baseline_clear(&self) -> Result<()> {
        match self {
            Self::Memory(memory) => lock(memory).baseline.clear(),
            Self::Postgres(pool) => {
                sqlx::query("DELETE FROM correlation_baseline")
                    .execute(pool)
                    .await?;
            }
        }
        Ok(())
    }

    pub async fn model_save(&self, name: &str, artifact: &[u8]) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                lock(memory)
                    .models
                    .insert(name.to_string(), artifact.to_vec());
            }
            Self::Postgres(pool) => {
                sqlx::query(
                    "INSERT INTO model_artifacts (name, artifact, updated_at) \
                     VALUES ($1, $2, now()) ON CONFLICT (name) DO UPDATE SET \
                     artifact = excluded.artifact, updated_at = excluded.updated_at",
                )
                .bind(name)
                .bind(artifact)
                .execute(pool)
                .await?;
            }
        }
        Ok(())
    }

    pub async fn model_load(&self, name: &str) -> Result<Option<Vec<u8>>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).models.get(name).cloned()),
            Self::Postgres(pool) => Ok(sqlx::query_scalar(
                "SELECT artifact FROM model_artifacts WHERE name = $1",
            )
            .bind(name)
            .fetch_optional(pool)
            .await?),
        }
    }
}
