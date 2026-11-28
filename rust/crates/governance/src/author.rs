//! The tool-calling runbook author. The model investigates through read-only
//! tools and can only finish by submitting a typed draft; the closed action set
//! is what rejects anything unsafe. The prompt only asks nicely.

use std::sync::LazyLock;

use intelliops_contracts::{Playbook, Timestamp, TraceStep, TraceStepKind};
use intelliops_llm::ChatClient;
use intelliops_store::Store;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};

use crate::context::SystemContext;

/// Prompts and tool schemas, exported from the Python implementation.
static PROMPTS: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../assets/author.json")).unwrap_or_default()
});

const MAX_COMPLETION_TOKENS: u32 = 1200;
/// The model uses a round per tool call: five read tools, then submit, plus
/// room to correct a rejected draft.
const MAX_ROUNDS: u32 = 9;
const TEXT_CAP: usize = 4000;
const NOTE_CAP: usize = 200;
const SUMMARY_CAP: usize = 120;
const PLACEHOLDER_ID: &str = "ai-draft-pending";

/// What drafting needs from a situation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DraftSituation {
    pub id: String,
    pub signature: String,
    pub severity: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Draft {
    pub playbook: Playbook,
    pub rationale: Option<String>,
    pub cited_facts: Vec<String>,
}

fn clip(text: &str, chars: usize) -> String {
    text.chars().take(chars).collect()
}

/// Receives each step of a run. A failure to record never stops drafting.
pub trait TraceSink: Send {
    fn record(&mut self, step: TraceStep) -> impl Future<Output = ()> + Send;
}

/// For a draft nobody is watching.
pub struct NoTrace;

impl TraceSink for NoTrace {
    async fn record(&mut self, _step: TraceStep) {}
}

pub struct Trace<S> {
    run_id: String,
    next_seq: i64,
    pub sink: S,
}

impl<S: TraceSink> Trace<S> {
    pub fn new(run_id: &str, sink: S) -> Self {
        Self {
            run_id: run_id.to_string(),
            next_seq: 0,
            sink,
        }
    }

    async fn emit(&mut self, kind: TraceStepKind, fill: impl FnOnce(&mut TraceStep)) {
        let mut step = TraceStep {
            run_id: self.run_id.clone(),
            seq: self.next_seq,
            kind,
            ts: Timestamp::now(),
            text: None,
            tool: None,
            arguments: None,
            result_summary: None,
            detail: None,
        };
        fill(&mut step);
        self.next_seq += 1;
        self.sink.record(step).await;
    }

    pub async fn outcome(&mut self, status: &str, proposal_id: Option<&str>) {
        let detail = json!({"status": status, "proposal_id": proposal_id});
        self.emit(TraceStepKind::Outcome, |s| {
            s.detail = detail.as_object().cloned();
        })
        .await;
    }
}

/// Read tools bound to one incident. A failure becomes `{"error": ...}`.
pub struct Toolbox {
    pub situation: DraftSituation,
    pub context: SystemContext,
    pub store: Store,
}

impl Toolbox {
    pub async fn dispatch(&self, name: &str, arguments: &Map<String, Value>) -> Value {
        let signature = arguments
            .get("signature")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let result = match name {
            "get_system_context" => Ok(json!({"context": self.context.summarize()})),
            // Always the incident in hand, never an id the model asks for.
            "get_incident_details" => Ok(json!(self.situation)),
            "get_past_outcomes" => self.past_outcomes(signature).await,
            "get_human_decisions" => self.human_decisions().await,
            "get_past_decisions" => self.past_decisions(signature).await,
            "list_available_actions" => Ok(json!({"actions": PROMPTS["action_notes"]})),
            _ => return json!({"error": format!("unknown or unhandled tool: {name}")}),
        };
        result.unwrap_or_else(|error| {
            tracing::warn!(%error, name, "author tool failed");
            json!({"error": "StoreError"})
        })
    }

    async fn past_outcomes(&self, signature: &str) -> Result<Value, intelliops_store::StoreError> {
        let mut records = self.store.training_all().await?;
        records.retain(|r| r.signature == signature);
        let mut by_playbook = Map::new();
        for record in &records {
            let entry = by_playbook
                .entry(record.playbook_id.clone())
                .or_insert_with(|| json!({"worked": 0, "total": 0}));
            entry["total"] = json!(entry["total"].as_u64().unwrap_or(0) + 1);
            if record.worked {
                entry["worked"] = json!(entry["worked"].as_u64().unwrap_or(0) + 1);
            }
        }
        records.sort_by_key(|r| std::cmp::Reverse(r.ts));
        let recent: Vec<Value> = records
            .iter()
            .take(5)
            .map(|r| json!({"playbook_id": r.playbook_id, "worked": r.worked, "ts": r.ts}))
            .collect();
        Ok(json!({"by_playbook": by_playbook, "recent": recent}))
    }

    async fn human_decisions(&self) -> Result<Value, intelliops_store::StoreError> {
        let mut records = self.store.audit_records(None).await?;
        records.retain(|r| {
            matches!(
                r.action.as_str(),
                "propose" | "approve-proposal" | "reject-proposal"
            )
        });
        records.sort_by_key(|r| std::cmp::Reverse(r.ts));
        let decisions: Vec<Value> = records
            .iter()
            .take(10)
            .map(|r| {
                json!({"action": r.action, "resource": r.resource,
                       "decided_by": r.actor, "ts": r.ts})
            })
            .collect();
        Ok(json!({
            "note": "recent proposal decisions (not scoped to this signature; \
                     audit keys on proposal id)",
            "decisions": decisions,
        }))
    }

    async fn past_decisions(&self, signature: &str) -> Result<Value, intelliops_store::StoreError> {
        let mut decisions = self.store.decisions_by_signature(signature).await?;
        for decision in &mut decisions {
            // Model free text replayed to a model is data, never instructions.
            decision.note = decision
                .note
                .as_ref()
                .filter(|n| !n.is_empty())
                .map(|n| format!("[prior unverified model note] {}", clip(n, NOTE_CAP)));
        }
        Ok(json!({"decisions": decisions}))
    }
}

/// One line per tool result for the activity feed, never the raw blob.
fn summarize(name: &str, result: &Value) -> String {
    if let Some(error) = result.get("error").and_then(Value::as_str) {
        return format!("error: {error}");
    }
    let count = |key: &str| {
        result[key]
            .as_array()
            .map(Vec::len)
            .or_else(|| result[key].as_object().map(Map::len))
            .unwrap_or(0)
    };
    match name {
        "get_past_outcomes" => match result["by_playbook"].as_object() {
            Some(by) if !by.is_empty() => by
                .iter()
                .map(|(id, c)| format!("{id} {}/{}", c["worked"], c["total"]))
                .collect::<Vec<_>>()
                .join(", "),
            _ => "no history".to_string(),
        },
        "get_system_context" => clip(result["context"].as_str().unwrap_or(""), SUMMARY_CAP),
        "get_past_decisions" => format!("{} prior decisions", count("decisions")),
        "get_human_decisions" => format!("{} recent decisions", count("decisions")),
        "get_incident_details" => format!(
            "signature={} severity={}",
            result["signature"].as_str().unwrap_or(""),
            result["severity"].as_str().unwrap_or("")
        ),
        "list_available_actions" => format!("{} actions", count("actions")),
        _ => clip(&result.to_string(), SUMMARY_CAP),
    }
}

/// Validate a `submit_runbook` call.
fn parse_submission(raw: &str) -> Result<Draft, String> {
    let parsed: Value = serde_json::from_str(raw).map_err(|e| e.to_string())?;
    let mut playbook = parsed["playbook"]
        .as_object()
        .cloned()
        .ok_or("playbook must be an object")?;
    // A model-chosen id is never trusted; the server assigns the real one.
    playbook
        .entry("id")
        .or_insert_with(|| PLACEHOLDER_ID.into());
    let cited_facts = match parsed.get("cited_facts") {
        None => Vec::new(),
        Some(facts) => facts
            .as_array()
            .ok_or("cited_facts must be an array")?
            .iter()
            .map(|f| f.as_str().map_or_else(|| f.to_string(), str::to_string))
            .collect(),
    };
    Ok(Draft {
        playbook: serde_json::from_value(Value::Object(playbook)).map_err(|e| e.to_string())?,
        rationale: Some(parsed["rationale"].as_str().unwrap_or("").to_string()),
        cited_facts,
    })
}

#[derive(Debug, Clone)]
pub struct Author {
    pub chat: ChatClient,
}

impl Author {
    /// `None` when the model could not produce a valid runbook.
    pub async fn draft<S: TraceSink>(
        &self,
        toolbox: &Toolbox,
        hint: Option<&str>,
        mut trace: Option<&mut Trace<S>>,
    ) -> Option<Draft> {
        let situation = &toolbox.situation;
        let mut messages = vec![
            json!({"role": "system", "content": PROMPTS["system"]}),
            json!({"role": "user", "content": format!(
                "Incident {} (severity {}, signature {}) has no matching runbook. Investigate \
                 using the available tools, then draft one. Hint: {}.",
                situation.id,
                situation.severity,
                situation.signature,
                hint.filter(|h| !h.is_empty()).unwrap_or("none"),
            )}),
        ];
        let mut nudged = false;
        for _ in 0..MAX_ROUNDS {
            let payload = json!({
                "model": self.chat.model,
                "messages": messages,
                "tools": PROMPTS["tools"],
                "tool_choice": "auto",
                "max_tokens": MAX_COMPLETION_TOKENS,
            });
            let message = self.chat.message(&payload).await.ok()?;
            let content = message["content"].clone();
            if let (Some(trace), Some(text)) = (trace.as_deref_mut(), content.as_str())
                && !text.is_empty()
            {
                let text = clip(text, TEXT_CAP);
                trace
                    .emit(TraceStepKind::ModelTurn, |s| s.text = Some(text))
                    .await;
            }
            let calls = message["tool_calls"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            if calls.is_empty() {
                // Nudge once, then let the round budget run out.
                messages.push(json!({"role": "assistant", "content": content}));
                if !nudged {
                    messages.push(json!({"role": "user", "content": PROMPTS["nudge"]}));
                    nudged = true;
                }
                continue;
            }
            nudged = false;
            messages.push(json!({"role": "assistant", "content": content, "tool_calls": calls}));
            for call in &calls {
                let name = call["function"]["name"].as_str().unwrap_or_default();
                let raw = call["function"]["arguments"].as_str().unwrap_or("{}");
                let result = if name == "submit_runbook" {
                    match parse_submission(raw) {
                        Ok(draft) => {
                            if let Some(trace) = trace.as_deref_mut() {
                                let detail = json!({
                                    "name": draft.playbook.name,
                                    "actions": draft.playbook.steps.iter()
                                        .map(|s| s.action).collect::<Vec<_>>(),
                                    "rationale": draft.rationale,
                                    "cited_facts": draft.cited_facts,
                                });
                                trace
                                    .emit(TraceStepKind::Submit, |s| {
                                        s.detail = detail.as_object().cloned();
                                    })
                                    .await;
                            }
                            return Some(draft);
                        }
                        Err(reason) => {
                            tracing::info!(reason, "invalid draft; asking again");
                            json!({"error": format!("invalid: {reason}; fix and resubmit")})
                        }
                    }
                } else {
                    let arguments: Map<String, Value> =
                        serde_json::from_str(raw).unwrap_or_default();
                    let result = toolbox.dispatch(name, &arguments).await;
                    if let Some(trace) = trace.as_deref_mut() {
                        let summary = summarize(name, &result);
                        trace
                            .emit(TraceStepKind::ToolCall, |s| {
                                s.tool = Some(name.to_string());
                                s.arguments = Some(arguments);
                                s.result_summary = Some(clip(&summary, TEXT_CAP));
                            })
                            .await;
                    }
                    result
                };
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result.to_string(),
                }));
            }
        }
        tracing::info!("author used every round without a valid draft");
        None
    }
}
