//! The tables of the Python stack, unchanged: promoted key columns plus a JSONB
//! `payload` that records are always rebuilt from.
//!
//! Two backends: Postgres (every deployment) and memory (tests and a
//! database-less run). The file backends of the Python stores are not carried
//! over (ADR-012).

mod agent;
mod audit;
mod correlation;
mod governance;
mod memory;
mod playbooks;

use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use sqlx::postgres::{PgPool, PgPoolOptions};

pub use correlation::BaselineRow;
pub use playbooks::load_seed_playbooks;

use crate::memory::Memory;

const SCHEMA: &str = include_str!("../migrations/schema.sql");
/// Any constant: serializes migration between services starting together.
const MIGRATION_LOCK: i64 = 0x0049_6e74_656c_6c69;
const ACQUIRE_TIMEOUT: Duration = Duration::from_secs(5);
const MAX_CONNECTIONS: u32 = 5;

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("database: {0}")]
    Database(#[from] sqlx::Error),
    #[error("stored record is unreadable: {0}")]
    Record(#[from] serde_json::Error),
    #[error("seed playbook {path}: {reason}")]
    Seed { path: String, reason: String },
}

pub type Result<T> = std::result::Result<T, StoreError>;

#[derive(Clone)]
pub enum Store {
    Memory(Arc<Mutex<Memory>>),
    Postgres(PgPool),
}

impl Store {
    #[must_use]
    pub fn memory() -> Self {
        Self::Memory(Arc::default())
    }

    /// Connects on first use, so a service starts while Postgres is coming up.
    /// Accepts SQLAlchemy URLs (`postgresql+psycopg://`).
    pub fn postgres(url: &str) -> Result<Self> {
        let url = url.replacen("postgresql+psycopg://", "postgresql://", 1);
        let pool = PgPoolOptions::new()
            .max_connections(MAX_CONNECTIONS)
            .acquire_timeout(ACQUIRE_TIMEOUT)
            .test_before_acquire(true)
            .connect_lazy(&url)?;
        Ok(Self::Postgres(pool))
    }

    /// Create whatever tables are missing. A no-op on an up-to-date database.
    pub async fn migrate(&self) -> Result<()> {
        let Self::Postgres(pool) = self else {
            return Ok(());
        };
        let mut transaction = pool.begin().await?;
        sqlx::query("SELECT pg_advisory_xact_lock($1)")
            .bind(MIGRATION_LOCK)
            .execute(&mut *transaction)
            .await?;
        sqlx::raw_sql(SCHEMA).execute(&mut *transaction).await?;
        transaction.commit().await?;
        Ok(())
    }

    pub async fn ping(&self) -> Result<()> {
        if let Self::Postgres(pool) = self {
            sqlx::query("SELECT 1").execute(pool).await?;
        }
        Ok(())
    }
}

/// Poisoning is impossible to act on here: every mutation is a single push or
/// replace, so the data stays consistent.
pub(crate) fn lock(memory: &Mutex<Memory>) -> MutexGuard<'_, Memory> {
    memory.lock().unwrap_or_else(PoisonError::into_inner)
}
