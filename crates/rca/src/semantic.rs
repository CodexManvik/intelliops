//! The embedding selector. Any failure means "no opinion", never an error.

use std::collections::HashMap;
use std::sync::{Mutex, PoisonError};

use intelliops_contracts::{Playbook, RootCauseHypothesis, Situation};
use intelliops_embed::{Embedder, cosine};

use crate::rank::Selector;

pub struct Semantic {
    embedder: Embedder,
    threshold: f64,
    /// Playbook symptom texts repeat on every incident.
    cache: Mutex<HashMap<String, Vec<f32>>>,
}

impl Semantic {
    #[must_use]
    pub fn new(embedder: Embedder, threshold: f64) -> Self {
        Self {
            embedder,
            threshold,
            cache: Mutex::default(),
        }
    }

    fn vector(&self, text: &str) -> Option<Vec<f32>> {
        let mut cache = self.cache.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(vector) = cache.get(text) {
            return Some(vector.clone());
        }
        let vector = self
            .embedder
            .encode(text)
            .map_err(|error| tracing::warn!(%error, "embedding failed"))
            .ok()?;
        cache.insert(text.to_string(), vector.clone());
        Some(vector)
    }
}

impl Selector for Semantic {
    fn score(
        &self,
        situation: &Situation,
        hypothesis: &RootCauseHypothesis,
        playbook: &Playbook,
    ) -> Option<f64> {
        let symptoms = playbook.symptoms.as_deref().filter(|s| !s.is_empty())?;
        let names = situation
            .member_events
            .iter()
            .map(|e| e.name.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        let query = format!("{}. signals: {names}", hypothesis.description);
        // The query is new every time: do not keep it.
        let query = self.embedder.encode(query.trim()).ok()?;
        cosine(&query, &self.vector(symptoms)?)
    }

    fn select(
        &self,
        situation: &Situation,
        hypothesis: &RootCauseHypothesis,
        playbooks: &[Playbook],
    ) -> Option<(String, f64)> {
        let mut best: Option<(String, f64)> = None;
        for playbook in playbooks {
            if let Some(score) = self.score(situation, hypothesis, playbook)
                && best.as_ref().is_none_or(|(_, top)| score > *top)
            {
                best = Some((playbook.id.clone(), score));
            }
        }
        best.filter(|(_, score)| *score >= self.threshold)
    }
}
