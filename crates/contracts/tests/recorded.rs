//! Held to what the Python implementation actually produced.

#![allow(clippy::unwrap_used, clippy::expect_used)]

use std::collections::BTreeMap;
use std::path::PathBuf;

use intelliops_contracts::*;
use serde::{Serialize, de::DeserializeOwned};
use serde_json::Value;

fn rust_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

#[test]
fn fingerprints_match_python() {
    let cases = std::fs::read_to_string(rust_dir().join("testdata/fingerprint.jsonl")).unwrap();
    let mut checked = 0;
    for line in cases.lines() {
        let case: Value = serde_json::from_str(line).unwrap();
        let labels: BTreeMap<String, String> =
            serde_json::from_value(case["args"][2].clone()).unwrap();
        let got = fingerprint(
            case["args"][0].as_str().unwrap(),
            case["args"][1].as_str().unwrap(),
            &labels,
        );
        assert_eq!(Some(got.as_str()), case["ok"].as_str(), "{line}");
        checked += 1;
    }
    assert!(checked >= 600, "only {checked} cases");
}

/// Decode every entry of a recorded stream, then check that what we write back
/// is the same JSON value Python wrote.
fn round_trip<T: DeserializeOwned + Serialize>(topic: &str) -> Vec<T> {
    let path = rust_dir().join(format!("testdata/golden/stream.{topic}.json"));
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path:?}: {e}"));
    let entries: Vec<(String, Vec<String>)> = serde_json::from_str(&raw).unwrap();
    entries
        .into_iter()
        .map(|(id, flat)| {
            let fields = flat
                .chunks(2)
                .map(|pair| (pair[0].as_str(), pair[1].as_str()));
            let envelope = Envelope::from_fields(fields).unwrap();
            let model: T = envelope
                .decode()
                .unwrap_or_else(|e| panic!("{topic} {id}: {e}"));
            let ours = serde_json::to_value(&model).unwrap();
            let theirs: Value = serde_json::from_str(&envelope.data).unwrap();
            assert_eq!(ours, theirs, "{topic} {id}");
            model
        })
        .collect()
}

#[test]
fn telemetry_round_trips_and_fingerprints_agree() {
    let events: Vec<TelemetryEvent> = round_trip(topics::TELEMETRY_RAW);
    assert!(!events.is_empty());
    for event in events {
        assert_eq!(
            fingerprint(&event.source, &event.name, &event.labels),
            event.fingerprint
        );
    }
}

#[test]
fn situations_round_trip_and_signatures_agree() {
    let situations: Vec<Situation> = round_trip(topics::SITUATIONS_DETECTED);
    assert!(!situations.is_empty());
    for situation in situations {
        let members = situation
            .member_events
            .iter()
            .map(|e| e.fingerprint.as_str());
        assert_eq!(signature(members), situation.signature);
        assert_eq!(situation_id(&situation.signature), situation.id);
    }
}

#[test]
fn diagnoses_and_outcomes_round_trip() {
    let diagnosed: Vec<DiagnosedSituation> = round_trip(topics::SITUATIONS_DIAGNOSED);
    let outcomes: Vec<RemediationOutcome> = round_trip(topics::REMEDIATION_OUTCOMES);
    assert!(!diagnosed.is_empty() && !outcomes.is_empty());
}

/// The governance API responses, which carry models the bus never does.
#[test]
fn governance_responses_round_trip() {
    fn check<T: DeserializeOwned + Serialize>(file: &str) {
        let path = rust_dir().join(format!("testdata/golden/{file}.json"));
        let theirs: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let models: Vec<T> = serde_json::from_value(theirs.clone()).unwrap();
        assert!(!models.is_empty(), "{file}");
        assert_eq!(serde_json::to_value(models).unwrap(), theirs, "{file}");
    }
    check::<Playbook>("governance_playbooks");
    check::<ApprovalRequest>("governance_approvals");
    check::<AuditRecord>("governance_audit");
}
