//! Accept and publish what the Python route did. The differences are
//! deliberate (ADR-010).

#![allow(clippy::unwrap_used)]

use intelliops_contracts::Timestamp;
use intelliops_ingestion::parse_batch;
use serde_json::Value;

/// Python kept an offset-less time naive; we read it as UTC.
fn as_utc(mut event: Value) -> Value {
    let ts = event["ts"].as_str().unwrap().to_string();
    let time = ts.split_once('T').unwrap().1;
    if !time.contains(['Z', '+', '-']) {
        event["ts"] = Value::String(format!("{ts}Z"));
    }
    event
}

#[test]
fn matches_the_recorded_python_route() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../app-cases/ingest.json");
    let cases: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    assert!(cases.len() >= 90);
    let now = Timestamp::now();
    let mut failures = Vec::new();
    for case in &cases {
        let body = case["body"].to_string();
        let accepted = case["status"] == 200;
        match parse_batch(body.as_bytes(), now) {
            Ok(events) if accepted => {
                let published = case["published"].as_array().unwrap();
                assert_eq!(events.len(), published.len(), "{body}");
                for (event, theirs) in events.iter().zip(published) {
                    let mut ours = serde_json::to_value(event).unwrap();
                    let mut theirs = theirs.clone();
                    // A missing ts is stamped with the time of the request.
                    if case["body"]["events"][0].get("ts").is_none() {
                        ours["ts"] = Value::Null;
                        theirs["ts"] = Value::Null;
                    } else {
                        theirs = as_utc(theirs);
                    }
                    if ours != theirs {
                        failures.push(format!("{body}\n  ours   {ours}\n  theirs {theirs}"));
                    }
                }
            }
            // Python answered 500 or 422; we always refuse with 422.
            Err(_) if !accepted => {}
            Ok(_) => failures.push(format!("{body}\n  we accept, Python refused")),
            Err(e) => failures.push(format!("{body}\n  we refuse: {e}")),
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
