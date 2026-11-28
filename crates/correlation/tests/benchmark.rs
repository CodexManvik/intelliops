//! The labelled benchmark streams. The online detectors must flag exactly what
//! Python's did; the forest's trees differ, so it must do at least as well.

#![allow(clippy::unwrap_used)]

use intelliops_contracts::{TelemetryEvent, Timestamp};
use intelliops_correlation::Scorer;
use intelliops_detect::{Detector, Robust, RobustConfig};
use serde_json::{Value, json};

const Z: f64 = 3.0;

fn scorer(kind: &str) -> Scorer {
    let robust = || {
        Robust::new(RobustConfig {
            warmup_samples: 30,
            ..RobustConfig::default()
        })
    };
    match kind {
        "river" => Scorer::River(Detector::new(50)),
        "robust" => Scorer::Robust(robust()),
        _ => Scorer::Trained(robust(), Box::default()),
    }
}

#[test]
fn detectors_hold_the_benchmark() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../testdata/benchmark.json");
    let scenarios: Vec<Value> =
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert_eq!(scenarios.len(), 5);
    for scenario in &scenarios {
        let name = scenario["scenario"].as_str().unwrap();
        let events: Vec<TelemetryEvent> = scenario["events"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| {
                serde_json::from_value(json!({
                    "source": "benchmark", "kind": "metric", "name": e[0], "value": e[1],
                    "ts": Timestamp::parse(e[2].as_str().unwrap()), "fingerprint": "f",
                }))
                .unwrap()
            })
            .collect();
        let labels: Vec<bool> = scenario["labels"]
            .as_array()
            .unwrap()
            .iter()
            .map(|l| l == 1)
            .collect();
        for kind in ["river", "robust", "trained"] {
            let mut detector = scorer(kind);
            if let Scorer::Trained(..) = detector {
                // Train on history, then serve.
                for event in &events {
                    detector.score(event);
                }
                if let Scorer::Trained(_, model) = &mut detector {
                    assert!(model.fit(), "{name}");
                }
            }
            let flagged: Vec<bool> = events.iter().map(|e| detector.score(e) > Z).collect();
            let count = |anomaly: bool| {
                flagged
                    .iter()
                    .zip(&labels)
                    .filter(|(f, l)| **f && **l == anomaly)
                    .count()
            };
            let (hits, false_alarms) = (count(true), count(false));
            let theirs = &scenario["results"][kind];
            let python = |key: &str| theirs[key].as_u64().unwrap() as usize;
            println!(
                "{name:18} {kind:8} tp={hits:3} (py {:3}) fp={false_alarms:3} (py {:3})",
                python("true_positives"),
                python("false_positives"),
            );
            if kind == "trained" {
                let normal = labels.iter().filter(|l| !**l).count();
                let rate = false_alarms as f64 / normal as f64;
                let allowed = theirs["false_positive_rate"].as_f64().unwrap() + 0.01;
                assert!(rate <= allowed, "{name}: false positive rate {rate}");
                assert!(hits + 3 >= python("true_positives"), "{name}: recall");
            } else {
                assert_eq!(hits, python("true_positives"), "{name} {kind}");
                assert_eq!(false_alarms, python("false_positives"), "{name} {kind}");
            }
        }
    }
}
