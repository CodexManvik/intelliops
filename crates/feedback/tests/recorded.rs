//! Labels, metrics and graduation exactly as the Python rules decided.

#![allow(clippy::unwrap_used)]

use intelliops_contracts::{RemediationOutcome, TrainingRecord};
use intelliops_feedback::{PlaybookStats, label, metrics};
use serde_json::{Value, json};

#[test]
fn matches_the_recorded_rules() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../testdata/feedback.json");
    let scenarios: Vec<Value> =
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(scenarios.len() >= 60);
    for (index, scenario) in scenarios.iter().enumerate() {
        let outcomes: Vec<RemediationOutcome> =
            serde_json::from_value(scenario["outcomes"].clone()).unwrap();
        let records: Vec<TrainingRecord> = outcomes.iter().map(label).collect();
        assert_eq!(
            serde_json::to_value(&records).unwrap(),
            scenario["records"],
            "scenario {index}"
        );
        assert_eq!(metrics(&records), scenario["metrics"], "scenario {index}");
        for (key, expected) in scenario["graduation"].as_object().unwrap() {
            let (playbook, simulated) = key.split_once(':').unwrap();
            let stats = PlaybookStats::of(&records, playbook, simulated == "1");
            let ours = json!({
                "successes": stats.successes,
                "failures": stats.failures,
                "rollbacks": stats.rollbacks,
                "graduate": stats.graduates(2),
            });
            assert_eq!(&ours, expected, "scenario {index} {key}");
        }
    }
}
