//! The same hypotheses, order, runbook and explanation as Python.

#![allow(clippy::unwrap_used)]

use std::collections::HashMap;
use std::path::PathBuf;

use intelliops_contracts::{
    DiagnosedSituation, EnrichmentContext, Playbook, RootCauseHypothesis, Situation,
};
use intelliops_rca::explain::template;
use intelliops_rca::rank::{Selector, diagnose};
use intelliops_store::load_seed_playbooks;
use serde_json::Value;

struct Table {
    scores: HashMap<String, f64>,
    pick: Option<(String, f64)>,
}

impl Selector for Table {
    fn score(&self, _: &Situation, _: &RootCauseHypothesis, p: &Playbook) -> Option<f64> {
        self.scores.get(&p.id).copied()
    }

    fn select(
        &self,
        _: &Situation,
        _: &RootCauseHypothesis,
        _: &[Playbook],
    ) -> Option<(String, f64)> {
        self.pick.clone()
    }
}

#[test]
fn matches_the_recorded_diagnoses() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let seeds = load_seed_playbooks(&repo.join("deploy/playbooks")).unwrap();
    let raw = std::fs::read_to_string(repo.join("testdata/rca.json")).unwrap();
    let cases: Vec<Value> = serde_json::from_str(&raw).unwrap();
    assert!(cases.len() >= 400);
    for (index, case) in cases.iter().enumerate() {
        let wanted: Vec<String> = serde_json::from_value(case["playbooks"].clone()).unwrap();
        let playbooks: Vec<Playbook> = seeds
            .iter()
            .filter(|p| wanted.contains(&p.id))
            .cloned()
            .collect();
        let context = EnrichmentContext {
            recent_deploys: serde_json::from_value(case["deploys"].clone()).unwrap(),
            ..EnrichmentContext::default()
        };
        let table = (!case["scores"].is_null()).then(|| Table {
            scores: serde_json::from_value(case["scores"].clone()).unwrap(),
            pick: serde_json::from_value(case["pick"].clone()).unwrap(),
        });
        let record: Option<HashMap<String, f64>> =
            serde_json::from_value(case["reliability"].clone()).unwrap();
        let lookup = |_: &str, runbook: &str| {
            record
                .as_ref()
                .and_then(|r| r.get(runbook).copied())
                .unwrap_or(0.0)
        };
        let situation: Situation = serde_json::from_value(case["situation"].clone()).unwrap();
        let mut ours = diagnose(
            situation,
            &context,
            &playbooks,
            table.as_ref().map(|t| t as &dyn Selector),
            record
                .is_some()
                .then_some(&lookup as &dyn Fn(&str, &str) -> f64),
        );
        if let Some(top) = ours.hypotheses.first_mut() {
            top.explanation = Some(template(top));
            top.explanation_source = Some("template".to_string());
        }
        let theirs: DiagnosedSituation = serde_json::from_value(case["diagnosed"].clone()).unwrap();
        assert_eq!(ours, theirs, "case {index}");
    }
}
