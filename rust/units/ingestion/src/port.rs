//! The Rust port of `services/ingestion/`.
//!
//! Replace each `unimplemented!` with the port of the source function. Return `Err` where
//! the source throws or raises: the caller gets an error there too. After changing a
//! signature, run `rust-anything refresh` to regenerate the bindings. `rust-anything diff` checks
//! behavior against what the source recorded.

#![forbid(unsafe_code)]

use std::collections::BTreeMap;

use sha1::{Digest, Sha1};

/// Port of `compute_fingerprint` (`services/ingestion/normalize.py:14`).
///
/// A series identity: sha1 of source, name and the key-sorted labels, first 16 hex
/// digits. Stored in Postgres and shown in the console, so it is byte-exact.
pub fn compute_fingerprint(
    source: String,
    name: String,
    labels: serde_json::Value,
) -> Result<String, String> {
    let Some(labels) = labels.as_object() else {
        return Err("TypeError: labels must be a mapping".to_string());
    };
    // Rust orders strings by UTF-8 bytes, which is the same order as Python's
    // code-point order.
    let mut sorted = BTreeMap::new();
    for (key, value) in labels {
        let Some(value) = value.as_str() else {
            return Err("TypeError: label values must be strings".to_string());
        };
        sorted.insert(key.as_str(), value);
    }
    let mut joined = format!("{source}|{name}");
    for (key, value) in sorted {
        joined.push('|');
        joined.push_str(key);
        joined.push('=');
        joined.push_str(value);
    }
    let mut hex = format!("{:x}", Sha1::digest(joined.as_bytes()));
    hex.truncate(16);
    Ok(hex)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fingerprint(labels: serde_json::Value) -> Result<String, String> {
        compute_fingerprint("prometheus".into(), "cpu_usage".into(), labels)
    }

    #[test]
    fn label_order_does_not_matter() {
        let one = fingerprint(json!({"a": "1", "b": "2"}));
        let two = fingerprint(json!({"b": "2", "a": "1"}));
        assert_eq!(one, two);
        assert_eq!(one.map(|h| h.len()), Ok(16));
    }

    #[test]
    fn rejects_non_string_labels() {
        assert!(fingerprint(json!({"a": 1})).is_err());
        assert!(fingerprint(json!(["a"])).is_err());
    }
}
