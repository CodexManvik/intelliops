//! The playbook registry.

use std::path::Path;

use intelliops_contracts::{HitlMode, Playbook};
use sqlx::types::Json;

use crate::{Result, Store, StoreError, lock};

/// Every `*.yaml` in `dir`, in file name order. A missing directory is empty.
pub fn load_seed_playbooks(dir: &Path) -> Result<Vec<Playbook>> {
    let failed = |path: &Path, reason: String| StoreError::Seed {
        path: path.display().to_string(),
        reason,
    };
    if !dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut paths = Vec::new();
    for entry in std::fs::read_dir(dir).map_err(|e| failed(dir, e.to_string()))? {
        let path = entry.map_err(|e| failed(dir, e.to_string()))?.path();
        if path.extension().is_some_and(|e| e == "yaml") {
            paths.push(path);
        }
    }
    paths.sort();
    paths
        .iter()
        .map(|path| {
            let text = std::fs::read_to_string(path).map_err(|e| failed(path, e.to_string()))?;
            serde_yaml_ng::from_str(&text).map_err(|e| failed(path, e.to_string()))
        })
        .collect()
}

fn mode(mode: HitlMode) -> &'static str {
    match mode {
        HitlMode::Auto => "auto",
        HitlMode::Hitl => "hitl",
        HitlMode::Disabled => "disabled",
    }
}

const INSERT: &str = "INSERT INTO playbooks (id, name, hitl_mode, reversible, payload, updated_at) \
                      VALUES ($1, $2, $3, $4, $5, now()) ON CONFLICT (id) DO ";
const REPLACE: &str = "UPDATE SET name = excluded.name, hitl_mode = excluded.hitl_mode, \
                       reversible = excluded.reversible, payload = excluded.payload, \
                       updated_at = excluded.updated_at";

impl Store {
    async fn playbook_write(&self, playbook: &Playbook, replace: bool) -> Result<()> {
        match self {
            Self::Memory(memory) => {
                let mut memory = lock(memory);
                if replace || !memory.playbooks.contains_key(&playbook.id) {
                    memory
                        .playbooks
                        .insert(playbook.id.clone(), playbook.clone());
                }
            }
            Self::Postgres(pool) => {
                let on_conflict = if replace { REPLACE } else { "NOTHING" };
                let statement = format!("{INSERT}{on_conflict}");
                sqlx::query(sqlx::AssertSqlSafe(statement))
                    .bind(&playbook.id)
                    .bind(&playbook.name)
                    .bind(mode(playbook.hitl_mode))
                    .bind(playbook.reversible)
                    .bind(Json(playbook))
                    .execute(pool)
                    .await?;
            }
        }
        Ok(())
    }

    /// Insert if absent: seeding runs on every start and must not undo a
    /// graduation made at run time.
    pub async fn playbook_seed(&self, playbooks: &[Playbook]) -> Result<()> {
        for playbook in playbooks {
            self.playbook_write(playbook, false).await?;
        }
        Ok(())
    }

    pub async fn playbook_register(&self, playbook: &Playbook) -> Result<()> {
        self.playbook_write(playbook, true).await
    }

    pub async fn playbook_get(&self, id: &str) -> Result<Option<Playbook>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).playbooks.get(id).cloned()),
            Self::Postgres(pool) => {
                let row: Option<Json<Playbook>> =
                    sqlx::query_scalar("SELECT payload FROM playbooks WHERE id = $1")
                        .bind(id)
                        .fetch_optional(pool)
                        .await?;
                Ok(row.map(|row| row.0))
            }
        }
    }

    /// Ordered by id.
    pub async fn playbook_list(&self) -> Result<Vec<Playbook>> {
        match self {
            Self::Memory(memory) => Ok(lock(memory).playbooks.values().cloned().collect()),
            Self::Postgres(pool) => {
                let rows: Vec<Json<Playbook>> =
                    sqlx::query_scalar("SELECT payload FROM playbooks ORDER BY id")
                        .fetch_all(pool)
                        .await?;
                Ok(rows.into_iter().map(|row| row.0).collect())
            }
        }
    }
}
