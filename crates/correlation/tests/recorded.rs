//! Replays what the Python engine did, operation by operation.

#![allow(clippy::unwrap_used)]

use intelliops_contracts::{Handling, Situation, TelemetryEvent, Timestamp};
use intelliops_correlation::{Engine, EngineConfig, Grouping, Scorer, SuppressionMode, Training};
use intelliops_detect::{Detector, Policy, Robust, RobustConfig, Thresholds};
use serde_json::Value;

fn engine(config: &Value) -> Engine {
    let uint = |key: &str| config[key].as_u64().unwrap();
    let scorer = if config["kind"] == "river" {
        Scorer::River(Detector::new(uint("warmup")))
    } else {
        Scorer::Robust(Robust::new(RobustConfig {
            warmup_samples: uint("warmup") as usize,
            window_size: uint("bucket_size") as usize,
            per_series: config["key_by"] == "series",
            ..RobustConfig::default()
        }))
    };
    Engine::new(
        EngineConfig {
            z_threshold: config["z_threshold"].as_f64().unwrap(),
            policy: if config["policy"] == true {
                Policy::On(Thresholds::default())
            } else {
                Policy::Off
            },
            window_seconds: config["window_seconds"].as_f64().unwrap(),
            grouping: if config["group_by"] == "service" {
                Grouping::Service
            } else {
                Grouping::Window
            },
            min_events: uint("min_events") as usize,
            suppress_mode: if config["suppress_mode"] == "quiet" {
                SuppressionMode::Quiet
            } else {
                SuppressionMode::Drop
            },
            suppress_min_samples: uint("suppress_min_samples") as u32,
            ..EngineConfig::default()
        },
        scorer,
    )
}

/// The last bit of `pow` and `sqrt` differs by platform.
fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-12 * a.abs().max(b.abs()).max(1.0)
}

fn check(ours: Option<&Situation>, theirs: &Value, at: &str) {
    let Some(ours) = ours else {
        assert!(theirs.is_null(), "{at}: Python emitted, we did not");
        return;
    };
    assert!(!theirs.is_null(), "{at}: we emitted, Python did not");
    assert_eq!(theirs["signature"], ours.signature.as_str(), "{at}");
    assert_eq!(theirs["severity"], ours.severity.as_str(), "{at}");
    let handling = match ours.handling {
        Handling::Normal => "normal",
        Handling::Quiet => "quiet",
    };
    assert_eq!(theirs["handling"], handling, "{at}");
    assert_eq!(
        theirs["members"].as_u64(),
        Some(ours.member_events.len() as u64),
        "{at}"
    );
    let time = |key: &str| Timestamp::parse(theirs[key].as_str().unwrap()).unwrap();
    assert_eq!(time("first_seen"), ours.first_seen, "{at}");
    assert_eq!(time("last_seen"), ours.last_seen, "{at}");
    assert!(
        close(
            theirs["peak_score"].as_f64().unwrap(),
            ours.peak_score.unwrap()
        ),
        "{at}: peak"
    );
    let mine = ours.baseline.as_ref().unwrap();
    let recorded = theirs["baseline"].as_object().unwrap();
    assert_eq!(mine.len(), recorded.len(), "{at}: baseline");
    for (name, stats) in recorded {
        for key in ["mean", "std"] {
            let (a, b) = (
                stats[key].as_f64().unwrap(),
                mine[name][key].as_f64().unwrap(),
            );
            assert!(close(a, b), "{at}: baseline {name} {key} {a} vs {b}");
        }
    }
}

#[test]
fn replays_the_python_engine() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../testdata/engine.json");
    let scenarios: Vec<Value> =
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(scenarios.len() >= 80);
    for (index, scenario) in scenarios.iter().enumerate() {
        let mut engine = engine(&scenario["config"]);
        for (step, op) in scenario["ops"].as_array().unwrap().iter().enumerate() {
            let at = format!("scenario {index} step {step}");
            match op["op"].as_str().unwrap() {
                "add" => {
                    let event: TelemetryEvent =
                        serde_json::from_value(op["event"].clone()).unwrap();
                    check(engine.add(event).as_ref(), &op["out"], &at);
                }
                "flush" => {
                    let out = engine.flush_all();
                    let expected = op["out"].as_array().unwrap();
                    assert_eq!(out.len(), expected.len(), "{at}: flush");
                    for (ours, theirs) in out.iter().zip(expected) {
                        check(Some(ours), theirs, &at);
                    }
                }
                "retrain" => {
                    let outcomes: Vec<Training> = op["records"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|r| Training {
                            signature: r["signature"].as_str().unwrap().to_string(),
                            worked: r["worked"].as_bool().unwrap(),
                        })
                        .collect();
                    engine.retrain(&outcomes);
                }
                "reset" => engine.reset(),
                other => panic!("{at}: unknown op {other}"),
            }
            for expected in op["suppressed"].as_array().unwrap() {
                check(engine.pop_suppressed().as_ref(), expected, &at);
            }
            assert!(engine.pop_suppressed().is_none(), "{at}: extra suppression");
        }
    }
}
