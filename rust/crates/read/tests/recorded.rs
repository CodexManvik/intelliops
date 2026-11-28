//! The same events give the console the same JSON.

#![allow(clippy::unwrap_used)]

use intelliops_read::ReadModel;
use serde_json::Value;

#[test]
fn matches_the_recorded_projection() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../app-cases/read.json");
    let scenarios: Vec<Value> =
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(scenarios.len() >= 30);
    let mut reads = 0;
    for (index, scenario) in scenarios.iter().enumerate() {
        let config = &scenario["config"];
        let mut model = ReadModel::new(
            config["max_outcomes"].as_u64().unwrap() as usize,
            config["ttl_seconds"].as_f64().unwrap(),
            config["max_situations"].as_u64().unwrap() as usize,
        );
        for (step, op) in scenario["ops"].as_array().unwrap().iter().enumerate() {
            let at = format!("scenario {index} step {step}");
            let event = || op["event"].clone();
            match op["op"].as_str().unwrap() {
                "detected" => {
                    model.apply_detected(&serde_json::from_value(event()).unwrap());
                }
                "diagnosed" => {
                    model.apply_diagnosed(&serde_json::from_value(event()).unwrap());
                }
                "suppressed" => model.apply_suppressed(),
                "outcome" => model.apply_outcome(&serde_json::from_value(event()).unwrap()),
                "reset" => model.reset(),
                _ => {
                    reads += 1;
                    let now = op["now_ms"].as_i64().unwrap();
                    let cards = serde_json::to_value(model.situations(Some(now))).unwrap();
                    assert_eq!(cards, op["situations"], "{at}: situations");
                    let outcomes = serde_json::to_value(model.outcomes()).unwrap();
                    assert_eq!(outcomes, op["outcomes"], "{at}: outcomes");
                    assert_eq!(model.metrics(), op["metrics"], "{at}: metrics");
                }
            }
        }
    }
    assert!(reads > 100, "only {reads} reads compared");
}
