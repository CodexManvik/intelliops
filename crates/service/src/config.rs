//! `INTELLIOPS_*` environment variables, with the Python service's defaults.

use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct Config {
    vars: HashMap<String, String>,
}

const PREFIX: &str = "INTELLIOPS_";

impl Config {
    #[must_use]
    pub fn from_env() -> Self {
        Self::from_vars(std::env::vars())
    }

    /// Names are matched without the prefix and without regard to case, as
    /// pydantic-settings does.
    #[must_use]
    pub fn from_vars(vars: impl IntoIterator<Item = (String, String)>) -> Self {
        let vars = vars
            .into_iter()
            .filter_map(|(key, value)| {
                let upper = key.to_ascii_uppercase();
                upper
                    .strip_prefix(PREFIX)
                    .map(|name| (name.to_ascii_lowercase(), value))
            })
            .collect();
        Self { vars }
    }

    #[must_use]
    pub fn get(&self, name: &str, default: &str) -> String {
        self.vars
            .get(name)
            .map_or_else(|| default.to_string(), String::clone)
    }

    /// A value that does not parse is an error: starting with a silently
    /// defaulted setting is worse than not starting.
    pub fn number<T: std::str::FromStr>(&self, name: &str, default: T) -> Result<T, String> {
        match self.vars.get(name) {
            None => Ok(default),
            Some(raw) => raw
                .trim()
                .parse()
                .map_err(|_| format!("{PREFIX}{}={raw:?} is not a number", name.to_uppercase())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(pairs: &[(&str, &str)]) -> Config {
        Config::from_vars(
            pairs
                .iter()
                .map(|(k, v)| ((*k).to_string(), (*v).to_string())),
        )
    }

    #[test]
    fn reads_prefixed_names_and_falls_back() {
        let c = config(&[("INTELLIOPS_REDIS_URL", "redis://x"), ("REDIS_URL", "no")]);
        assert_eq!(c.get("redis_url", "d"), "redis://x");
        assert_eq!(c.get("log_level", "INFO"), "INFO");
    }

    #[test]
    fn rejects_a_bad_number() {
        let c = config(&[("INTELLIOPS_BUS_STREAM_MAXLEN", "lots")]);
        assert!(c.number("bus_stream_maxlen", 1usize).is_err());
        assert_eq!(c.number("telemetry_poll_seconds", 5.0), Ok(5.0));
    }
}
