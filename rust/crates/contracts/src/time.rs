//! Timestamps as lenient as Pydantic's on the way in, canonical on the way out.

use chrono::{DateTime, FixedOffset, NaiveDateTime, SecondsFormat, Utc};
use serde::{Deserialize, Deserializer, Serialize, Serializer, de};

/// An instant with its offset. A value without an offset is read as UTC (Python
/// kept it naive, which could not be compared with aware values: ADR-010).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Timestamp(pub DateTime<FixedOffset>);

/// Numbers above this are milliseconds since the epoch, as in Pydantic.
const MILLIS_WATERSHED: f64 = 2e10;

impl Timestamp {
    #[must_use]
    pub fn now() -> Self {
        Self(Utc::now().fixed_offset())
    }

    #[must_use]
    pub fn parse(text: &str) -> Option<Self> {
        let text = text.trim();
        if let Ok(aware) = DateTime::parse_from_rfc3339(text) {
            return Some(Self(aware));
        }
        for format in ["%Y-%m-%d %H:%M:%S%.f%:z", "%Y-%m-%dT%H:%M:%S%.f%z"] {
            if let Ok(aware) = DateTime::parse_from_str(text, format) {
                return Some(Self(aware));
            }
        }
        for format in [
            "%Y-%m-%dT%H:%M:%S%.f",
            "%Y-%m-%d %H:%M:%S%.f",
            "%Y-%m-%dT%H:%M",
        ] {
            if let Ok(naive) = NaiveDateTime::parse_from_str(text, format) {
                return Some(Self(naive.and_utc().fixed_offset()));
            }
        }
        text.parse::<f64>().ok().and_then(Self::from_epoch)
    }

    #[must_use]
    pub fn from_epoch(value: f64) -> Option<Self> {
        if !value.is_finite() {
            return None;
        }
        let seconds = if value.abs() > MILLIS_WATERSHED {
            value / 1000.0
        } else {
            value
        };
        let micros = (seconds * 1e6).round() as i64;
        DateTime::from_timestamp_micros(micros).map(|utc| Self(utc.fixed_offset()))
    }
}

impl std::fmt::Display for Timestamp {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let precision = if self.0.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        };
        f.write_str(&self.0.to_rfc3339_opts(precision, true))
    }
}

impl Serialize for Timestamp {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_str(self)
    }
}

impl<'de> Deserialize<'de> for Timestamp {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Raw {
            Text(String),
            Number(f64),
        }
        match Raw::deserialize(deserializer)? {
            Raw::Text(text) => Self::parse(&text),
            Raw::Number(number) => Self::from_epoch(number),
        }
        .ok_or_else(|| de::Error::custom("invalid timestamp"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn canonical(text: &str) -> Option<String> {
        Timestamp::parse(text).map(|t| t.to_string())
    }

    #[test]
    fn writes_what_pydantic_writes() {
        let micros = "2026-11-28T13:45:49.006000Z";
        assert_eq!(canonical(micros).as_deref(), Some(micros));
        assert_eq!(
            canonical("2026-01-02T03:04:05+00:00").as_deref(),
            Some("2026-01-02T03:04:05Z")
        );
        assert_eq!(
            canonical("2026-01-02T03:04:05+05:30").as_deref(),
            Some("2026-01-02T03:04:05+05:30")
        );
    }

    #[test]
    fn accepts_the_loose_forms_ingest_receives() {
        for text in [
            "2026-01-02 03:04:05",
            "2026-01-02T03:04:05",
            "1767323045",
            "1767323045000",
        ] {
            assert_eq!(
                canonical(text).as_deref(),
                Some("2026-01-02T03:04:05Z"),
                "{text}"
            );
        }
        assert_eq!(canonical("yesterday"), None);
    }
}
