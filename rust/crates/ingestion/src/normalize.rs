//! One canonical event shape, accepting what the Python route accepted
//! (`app-cases/ingest.json` records it).

use std::collections::BTreeMap;

use intelliops_contracts::{TelemetryEvent, TelemetryKind, Timestamp, fingerprint};
use serde_json::{Map, Value};

fn text(raw: &Map<String, Value>, field: &str) -> Result<String, String> {
    match raw.get(field) {
        Some(Value::String(value)) => Ok(value.clone()),
        Some(_) => Err(format!("{field} must be a string")),
        None => Err(format!("{field} is required")),
    }
}

/// A number, a boolean (1 or 0) or numeric text such as `" 2 "` or `"1_000"`.
fn number(value: &Value) -> Result<Option<f64>, String> {
    let invalid = || "value must be a number".to_string();
    match value {
        Value::Null => Ok(None),
        Value::Bool(flag) => Ok(Some(f64::from(u8::from(*flag)))),
        Value::Number(n) => n.as_f64().map(Some).ok_or_else(invalid),
        Value::String(raw) => {
            let trimmed = raw.trim();
            // Underscores are allowed between digits only.
            let bytes = trimmed.as_bytes();
            let digit = |i: usize| bytes.get(i).is_some_and(u8::is_ascii_digit);
            let misplaced = bytes
                .iter()
                .enumerate()
                .any(|(i, b)| *b == b'_' && !(i > 0 && digit(i - 1) && digit(i + 1)));
            if misplaced {
                return Err(invalid());
            }
            trimmed
                .replace('_', "")
                .parse()
                .map(Some)
                .map_err(|_| invalid())
        }
        _ => Err(invalid()),
    }
}

/// An absent or empty value of any JSON type means "no labels".
fn labels(value: Option<&Value>) -> Result<BTreeMap<String, String>, String> {
    let invalid = || "labels must be an object of strings".to_string();
    let empty = match value {
        None | Some(Value::Null | Value::Bool(false)) => true,
        Some(Value::Number(n)) => n.as_f64() == Some(0.0),
        Some(Value::String(s)) => s.is_empty(),
        Some(Value::Array(a)) => a.is_empty(),
        _ => false,
    };
    if empty {
        return Ok(BTreeMap::new());
    }
    let Some(Value::Object(object)) = value else {
        return Err(invalid());
    };
    object
        .iter()
        .map(|(key, value)| match value {
            Value::String(value) => Ok((key.clone(), value.clone())),
            _ => Err(invalid()),
        })
        .collect()
}

/// `now` stamps an event that arrives without a `ts`.
pub fn normalize(raw: &Value, now: Timestamp) -> Result<TelemetryEvent, String> {
    let Value::Object(raw) = raw else {
        return Err("each event must be an object".to_string());
    };
    let source = text(raw, "source")?;
    let name = text(raw, "name")?;
    let kind: TelemetryKind = raw
        .get("kind")
        .ok_or("kind is required")
        .and_then(|k| serde_json::from_value(k.clone()).map_err(|_| "unknown kind"))?;
    let ts: Timestamp = match raw.get("ts") {
        None => now,
        Some(value) => serde_json::from_value(value.clone()).map_err(|_| "invalid ts")?,
    };
    let payload = match raw.get("payload") {
        None | Some(Value::Null) => None,
        Some(Value::Object(object)) => Some(object.clone()),
        Some(_) => return Err("payload must be an object".to_string()),
    };
    let labels = labels(raw.get("labels"))?;
    Ok(TelemetryEvent {
        fingerprint: fingerprint(&source, &name, &labels),
        value: number(raw.get("value").unwrap_or(&Value::Null))?,
        source,
        kind,
        name,
        payload,
        labels,
        ts,
    })
}
