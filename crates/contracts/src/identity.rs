//! Identifiers that are stored in Postgres and shown in the console. They must
//! stay byte-identical to the Python implementation (ADR-003).

use std::collections::{BTreeMap, BTreeSet};

use sha1::{Digest, Sha1};

const SITUATION_PREFIX: &str = "sit-";
const ID_HEX_CHARS: usize = 16;

fn short_sha1(text: &str) -> String {
    Sha1::digest(text.as_bytes())
        .iter()
        .take(ID_HEX_CHARS / 2)
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// A series identity: source, name and labels. Every sample of one series
/// shares it. `BTreeMap` orders by UTF-8 bytes, which matches Python's
/// code-point ordering.
#[must_use]
pub fn fingerprint(source: &str, name: &str, labels: &BTreeMap<String, String>) -> String {
    let mut joined = format!("{source}|{name}");
    for (key, value) in labels {
        joined.push_str(&format!("|{key}={value}"));
    }
    short_sha1(&joined)
}

/// An incident identity: the *set* of series involved, so the number of samples
/// that landed in the window does not change it.
#[must_use]
pub fn signature<'a>(fingerprints: impl IntoIterator<Item = &'a str>) -> String {
    let unique: BTreeSet<&str> = fingerprints.into_iter().collect();
    short_sha1(&unique.into_iter().collect::<Vec<_>>().join("|"))
}

#[must_use]
pub fn situation_id(signature: &str) -> String {
    format!("{SITUATION_PREFIX}{signature}")
}

#[must_use]
pub fn signature_from_situation_id(id: &str) -> &str {
    id.strip_prefix(SITUATION_PREFIX).unwrap_or(id)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signature_ignores_order_and_repeats() {
        assert_eq!(signature(["b", "a", "a"]), signature(["a", "b"]));
    }

    #[test]
    fn situation_ids_round_trip() {
        assert_eq!(signature_from_situation_id(&situation_id("abc")), "abc");
        assert_eq!(signature_from_situation_id("abc"), "abc");
    }
}
