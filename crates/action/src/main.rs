use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use intelliops_action::gates::{ACTOR, Settings};
use intelliops_action::governance::HttpGate;
use intelliops_action::kubernetes::{KubeHealth, KubeRemediator, MetricSource};
use intelliops_action::sandbox::CloneSandbox;
use intelliops_action::{Gate, Health, Rehearsal, Remediator, execute};
use intelliops_bus::{Bus, Consumer};
use intelliops_contracts::{
    AuditRecord, DiagnosedSituation, Handling, HitlMode, Playbook, PreflightResult,
    RemediationOutcome, RemediationPlan, RemediationResult, RemediationTarget, Situation,
    Timestamp, topics,
};
use intelliops_detect::{Policy, Thresholds};
use intelliops_service::{
    Config, Failure, Service, app, bus, consumer_config, port, publish, serve, start, store,
};
use intelliops_store::Store;
use kube::Client;

const RETRY: Duration = Duration::from_secs(2);
const DONE: &str = "done";

#[tokio::main]
async fn main() -> ExitCode {
    start("action", run).await
}

/// Dry run logs the plan and succeeds without touching anything.
enum Hands {
    DryRun,
    Kube(KubeRemediator),
}

impl Remediator for Hands {
    async fn execute(&mut self, plan: &RemediationPlan) -> bool {
        match self {
            Self::DryRun => {
                for step in &plan.steps {
                    tracing::info!(target = %plan.target.deployment, action = ?step.action,
                        "dry run: execute");
                }
                true
            }
            Self::Kube(remediator) => remediator.execute(plan).await,
        }
    }

    async fn rollback(&mut self, plan: &RemediationPlan) -> bool {
        match self {
            Self::DryRun => {
                tracing::info!(target = %plan.target.deployment, "dry run: rollback");
                true
            }
            Self::Kube(remediator) => remediator.rollback(plan).await,
        }
    }
}

enum Check {
    Always,
    Kube(Box<KubeHealth>),
}

impl Health for Check {
    async fn check(&self, situation: &Situation, target: &RemediationTarget) -> bool {
        match self {
            Self::Always => true,
            Self::Kube(health) => health.check(situation, target).await,
        }
    }
}

enum Sandbox {
    Off,
    Clone(CloneSandbox),
}

impl Rehearsal for Sandbox {
    async fn rehearse(&self, situation: &Situation, plan: &RemediationPlan) -> PreflightResult {
        match self {
            Self::Off => PreflightResult {
                passed: true,
                detail: "not rehearsed (sandbox off)".to_string(),
                mode: "off".to_string(),
                sandbox_namespace: None,
            },
            Self::Clone(sandbox) => sandbox.rehearse(situation, plan).await,
        }
    }
}

struct Action {
    bus: Bus,
    store: Store,
    gate: HttpGate,
    remediator: Hands,
    health: Check,
    sandbox: Sandbox,
    namespace: String,
    mode: String,
    /// Remember executions, so a redelivery cannot run one twice.
    claim_seconds: Option<u64>,
    quiet_threshold: f64,
    quiet_min_samples: usize,
    quiet_skip_approval: bool,
}

/// An outcome for which nothing was attempted.
fn untouched(
    situation: &Situation,
    playbook_id: String,
    result: RemediationResult,
    reason: &str,
) -> RemediationOutcome {
    RemediationOutcome {
        situation_id: situation.id.clone(),
        playbook_id,
        result,
        health_after: reason.to_string(),
        ts: Timestamp::now(),
        hitl_mode: HitlMode::Disabled,
        steps: Vec::new(),
        mode: "none".to_string(),
        preflight: None,
        handling: Handling::Normal,
    }
}

impl Action {
    async fn note(&self, situation_id: &str, action: &str, decision: String) {
        self.gate
            .audit(&AuditRecord {
                actor: ACTOR.to_string(),
                action: action.to_string(),
                resource: format!("situation:{situation_id}"),
                decision,
                ts: Timestamp::now(),
                correlation_id: situation_id.to_string(),
            })
            .await;
    }

    /// May a quiet situation's playbook run unasked? Correlation's mark is only
    /// a request: this checks this playbook's real record on this signature.
    async fn quiet_approval(&self, situation: &Situation, playbook: &Playbook) -> bool {
        // An automatic playbook asks nobody anyway.
        if playbook.hitl_mode != HitlMode::Hitl {
            return false;
        }
        let (eligible, evidence) = if !self.quiet_skip_approval {
            (false, "quiet approval disabled".to_string())
        } else {
            match self.store.training_all().await {
                Ok(records) => {
                    let matching: Vec<_> = records
                        .iter()
                        .filter(|r| {
                            r.is_real()
                                && r.signature == situation.signature
                                && r.playbook_id == playbook.id
                        })
                        .collect();
                    let total = matching.len();
                    let worked = matching.iter().filter(|r| r.worked).count();
                    let eligible = total >= self.quiet_min_samples.max(1)
                        && worked as f64 / total as f64 >= self.quiet_threshold;
                    (eligible, format!("{worked}/{total} real runs worked"))
                }
                // No evidence means asking a human.
                Err(error) => {
                    tracing::warn!(%error, "track record unreadable");
                    (false, "no track record available".to_string())
                }
            }
        };
        let decision = if eligible {
            "quiet:skip-approval"
        } else {
            "quiet:needs-approval"
        };
        tracing::info!(situation_id = %situation.id, playbook_id = %playbook.id, eligible,
            evidence, "quiet situation");
        self.note(
            &situation.id,
            "quiet-handling",
            format!("{decision} ({evidence})"),
        )
        .await;
        eligible
    }

    async fn remediate(&mut self, diagnosed: &DiagnosedSituation) -> RemediationOutcome {
        let situation = &diagnosed.situation;
        let suggested = diagnosed
            .suggested_runbook_id
            .as_deref()
            .filter(|id| !id.is_empty());
        let playbook = match suggested {
            None => None,
            Some(id) => loop {
                match self.store.playbook_get(id).await {
                    Ok(playbook) => break playbook,
                    Err(error) => {
                        tracing::warn!(%error, "cannot read the registry; retrying");
                        tokio::time::sleep(RETRY).await;
                    }
                }
            },
        };
        let Some(playbook) = playbook else {
            // "Nothing to suggest" and "unknown runbook" are different bugs.
            let reason = if suggested.is_none() {
                "escalated:no-diagnosis"
            } else {
                "escalated:unknown-runbook"
            };
            self.note(&situation.id, "escalate", "escalated".to_string())
                .await;
            return untouched(
                situation,
                suggested.unwrap_or_default().to_string(),
                RemediationResult::Escalated,
                reason,
            );
        };
        let quiet = situation.handling == Handling::Quiet;
        let skip = quiet && self.quiet_approval(situation, &playbook).await;
        let mut outcome = execute(
            situation,
            &playbook,
            &self.gate,
            &mut self.remediator,
            &self.health,
            &self.sandbox,
            &Settings {
                namespace: &self.namespace,
                mode: &self.mode,
            },
            skip,
        )
        .await;
        if quiet && (skip || playbook.hitl_mode == HitlMode::Auto) {
            outcome.handling = Handling::Quiet;
        }
        outcome
    }

    async fn handle(&mut self, diagnosed: DiagnosedSituation) {
        let situation = &diagnosed.situation;
        // Per occurrence: a recurrence of the same shape is a new execution.
        let key = format!("action:exec:{}:{}", situation.id, situation.first_seen);
        let outcome = if let Some(ttl) = self.claim_seconds {
            match self.bus.claim(&key, ttl).await {
                Ok(true) => Some(self.remediate(&diagnosed).await),
                Ok(false) => match self.bus.state(&key).await {
                    Ok(Some(state)) if state == DONE => None,
                    // A previous attempt died part way. Never guess whether the
                    // cluster changed: say so and let a human look.
                    _ => {
                        self.note(&situation.id, "interrupted", "unknown-if-mutated".into())
                            .await;
                        Some(untouched(
                            situation,
                            String::new(),
                            RemediationResult::Failure,
                            "interrupted:unknown",
                        ))
                    }
                },
                Err(error) => {
                    // Without the claim a repeat cannot be ruled out: do not act.
                    tracing::error!(%error, "cannot claim the execution; not acting");
                    Some(untouched(
                        situation,
                        String::new(),
                        RemediationResult::Failure,
                        "interrupted:unknown",
                    ))
                }
            }
        } else {
            Some(self.remediate(&diagnosed).await)
        };
        let Some(outcome) = outcome else { return };
        publish(&self.bus, topics::REMEDIATION_OUTCOMES, &outcome).await;
        if let Some(ttl) = self.claim_seconds
            && let Err(error) = self.bus.set_state(&key, DONE, ttl).await
        {
            tracing::warn!(%error, "could not mark the execution done");
        }
    }

    /// One at a time: two remediations must not race on a cluster.
    async fn consume(mut self, mut consumer: Consumer) {
        loop {
            let diagnosed: DiagnosedSituation = consumer.next().await;
            self.handle(diagnosed).await;
        }
    }
}

fn seconds(config: &Config, name: &str, default: f64) -> Result<Duration, Failure> {
    Ok(Duration::try_from_secs_f64(config.number(name, default)?)?)
}

fn policy(config: &Config) -> Result<Policy, String> {
    if config.get("detection_policy", "off") != "on" {
        return Ok(Policy::Off);
    }
    let d = Thresholds::default();
    Ok(Policy::On(Thresholds {
        ratio: config.number("detection_ratio_threshold", d.ratio)?,
        saturation_ratio: config
            .number("detection_saturation_ratio_threshold", d.saturation_ratio)?,
        saturation_percent: config.number(
            "detection_saturation_percent_threshold",
            d.saturation_percent,
        )?,
        latency_ceiling_ms: config.number("detection_latency_ceiling_ms", d.latency_ceiling_ms)?,
    }))
}

async fn run(config: Config) -> Result<(), Failure> {
    let bus = bus(&config)?;
    let store = store(&config).await?;
    let mode = config.get("remediator_mode", "dry_run");
    let health_mode = config.get("health_check_mode", "always");
    let sandbox_mode = config.get("sandbox_mode", "off");
    let namespace = config.get("k8s_namespace", "intelliops-demo");
    let needs_cluster = mode == "k8s" || health_mode == "k8s" || sandbox_mode == "k8s";
    // Asked for a cluster and cannot reach one: fail the start, never fall
    // back to pretending.
    let cluster = if needs_cluster {
        Some(Client::try_default().await?)
    } else {
        None
    };

    let remediator = match &cluster {
        Some(client) if mode == "k8s" => Hands::Kube(KubeRemediator::new(client.clone())),
        _ => Hands::DryRun,
    };
    let health = match &cluster {
        Some(client) if health_mode == "k8s" => Check::Kube(Box::new(KubeHealth {
            client: client.clone(),
            metrics: Some(MetricSource {
                client: reqwest::Client::new(),
                prometheus_url: config.get("prometheus_url", "http://localhost:9090"),
                policy: policy(&config)?,
                z_threshold: config.number("correlation_z_threshold", 3.0)?,
            }),
            timeout: seconds(&config, "health_check_timeout_seconds", 90.0)?,
            poll: Duration::from_secs(2),
        })),
        _ => Check::Always,
    };
    let sandbox = match &cluster {
        Some(client) if sandbox_mode == "k8s" => Sandbox::Clone(CloneSandbox {
            client: client.clone(),
            namespace: namespace.clone(),
        }),
        _ => Sandbox::Off,
    };

    let token = config.get("auth_token", "");
    let gate = HttpGate::new(
        &config.get("governance_url", "http://localhost:8005"),
        (config.get("auth_mode", "off") == "token" && !token.is_empty()).then_some(token),
        seconds(&config, "hitl_poll_timeout_seconds", 30.0)?,
        seconds(&config, "hitl_poll_interval_seconds", 0.5)?,
    )?;
    let quiet_skip = config
        .get("quiet_skip_approval", "true")
        .eq_ignore_ascii_case("true");
    let claim_seconds = if config.get("bus_idempotency_mode", "off") == "off" {
        None
    } else {
        Some(config.number("bus_idempotency_ttl_seconds", 86_400)?)
    };
    let action = Action {
        bus: bus.clone(),
        store: store.clone(),
        gate,
        remediator,
        health,
        sandbox,
        namespace,
        mode,
        claim_seconds,
        quiet_threshold: config.number("reliability_suppress_threshold", 0.8)?,
        quiet_min_samples: config.number("reliability_suppress_min_samples", 3)?,
        quiet_skip_approval: quiet_skip,
    };
    let diagnosed = consumer_config(&config, topics::SITUATIONS_DIAGNOSED, "action")?;
    tokio::spawn(action.consume(bus.consumer(diagnosed)?));

    let service = Service::new("action-service", config, bus).with_database(store);
    serve(app(Arc::new(service), Router::new()), port()).await?;
    Ok(())
}
