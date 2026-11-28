//! Bit-for-bit against what the Python detector did.

#![allow(clippy::unwrap_used, clippy::expect_used)]

use intelliops_detect::{BaselineRow, Detector, MetricKind, Policy, Thresholds};
use serde_json::Value;

fn scenarios() -> Vec<Value> {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../testdata/detector.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn scores_decisions_and_baselines_match_python() {
    let all = scenarios();
    assert!(all.len() >= 60);
    for (index, case) in all.iter().enumerate() {
        let mut detector = Detector::new(case["warmup"].as_u64().unwrap());
        let threshold = case["threshold"].as_f64().unwrap();
        let policy = if case["policy"].as_bool().unwrap() {
            Policy::On(Thresholds::default())
        } else {
            Policy::Off
        };
        for (step_no, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            let name = step["name"].as_str().unwrap();
            let value = step["value"].as_f64();
            let score = detector.detect(name, value);
            let expected = step["score"].as_f64().unwrap();
            // The only inexact operation is `** 0.5`, whose last bit depends on
            // the platform's libm (for Python too). Recorded on Windows.
            assert!(
                (score - expected).abs() <= 1e-12 * expected.abs().max(1.0),
                "scenario {index} step {step_no}: {score} vs {expected}"
            );
            assert_eq!(
                policy.is_anomaly(name, value, score, threshold),
                step["anomaly"].as_bool().unwrap(),
                "scenario {index} step {step_no}"
            );
        }
        let rows: Vec<BaselineRow> = serde_json::from_value(case["snapshot"].clone()).unwrap();
        assert_eq!(detector.snapshot(), rows, "scenario {index}");
    }
}

#[test]
fn classification_matches_python() {
    for (name, class) in scenarios()[0]["classes"].as_object().unwrap() {
        let ours = format!("{:?}", MetricKind::classify(name)).to_lowercase();
        assert_eq!(Some(ours.as_str()), class.as_str(), "{name}");
    }
}

#[test]
fn a_loaded_baseline_scores_like_the_original() {
    let mut original = Detector::new(2);
    for value in [10.0, 12.0, 11.0, 13.0] {
        original.detect("m", Some(value));
    }
    let mut restored = Detector::new(2);
    restored.load(&original.snapshot());
    let (a, b) = (
        original.detect("m", Some(40.0)),
        restored.detect("m", Some(40.0)),
    );
    assert!((a - b).abs() < 1e-9, "{a} vs {b}");
}
