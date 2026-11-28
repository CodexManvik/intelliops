//! The curated description of the target system the author drafts for. A
//! missing or placeholder file reads as unconfigured.

use std::path::PathBuf;

use serde_json::Value;

const UNCONFIGURED: &str = "System context: unconfigured (no target system described yet).";

#[derive(Debug, Clone)]
pub struct SystemContext(pub PathBuf);

fn text(value: &Value) -> String {
    value
        .as_str()
        .map_or_else(|| value.to_string(), str::to_string)
}

fn list(value: &Value) -> String {
    let items: Vec<String> = value
        .as_array()
        .map(|a| a.iter().map(text).collect())
        .unwrap_or_default();
    if items.is_empty() {
        "none".to_string()
    } else {
        items.join(", ")
    }
}

impl SystemContext {
    fn load(&self) -> Option<Value> {
        let raw = std::fs::read_to_string(&self.0).ok()?;
        let data: Value = serde_yaml_ng::from_str(&raw).ok()?;
        let named = data["system"]["name"]
            .as_str()
            .is_some_and(|n| !n.is_empty());
        let services = data["services"].as_array().is_some_and(|s| !s.is_empty());
        (data.is_object() && (named || services)).then_some(data)
    }

    #[must_use]
    pub fn summarize(&self) -> String {
        let Some(data) = self.load() else {
            return UNCONFIGURED.to_string();
        };
        let field = |value: &Value| value.as_str().unwrap_or("").to_string();
        let system = &data["system"];
        let mut lines = vec![
            format!(
                "System: {} — {}",
                system["name"].as_str().unwrap_or("?"),
                field(&system["summary"])
            )
            .trim()
            .to_string(),
        ];
        for service in data["services"].as_array().into_iter().flatten() {
            lines.push(format!(
                "- {}: {}; depends on {}; metrics {}",
                service["name"].as_str().unwrap_or("?"),
                field(&service["role"]),
                list(&service["depends_on"]),
                list(&service["key_metrics"]),
            ));
        }
        if let Some(actions) = data["actions"].as_object()
            && !actions.is_empty()
        {
            let notes: Vec<String> = actions
                .iter()
                .filter(|(_, v)| !v.is_null() && v.as_str() != Some(""))
                .map(|(k, v)| format!("{k}={}", text(v)))
                .collect();
            lines.push(format!("Action notes: {}", notes.join("; ")));
        }
        if let Some(notes) = data["notes"].as_str().filter(|n| !n.is_empty()) {
            lines.push(format!("Notes: {notes}"));
        }
        lines.join("\n")
    }
}
