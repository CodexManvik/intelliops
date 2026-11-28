//! The safety heart. Gates in order: disabled, not reversible, RBAC,
//! destructive shape, then a rehearsal, then a human. Only then is anything
//! executed, verified, and rolled back if unhealthy. Every path produces an
//! outcome and an audit record.

use std::future::Future;

use intelliops_contracts::{
    Action, ApprovalRequest, AuditRecord, Handling, HitlMode, Playbook, PreflightResult, ProbeKind,
    RemediationOutcome, RemediationPlan, RemediationResult, RemediationStep, RemediationTarget,
    Situation, Timestamp,
};

pub const ACTOR: &str = "action-service";

/// A scale delta this large can zero any deployment (the ceiling is 10).
const SCALE_TAKEDOWN_DELTA: i64 = -10;
const MIN_CPU_MILLICORES: f64 = 10.0;
const MIN_MEM_MEBIBYTES: f64 = 16.0;

/// The synchronous seam to governance. Every method fails closed.
pub trait Gate: Send + Sync {
    fn allowed(&self, resource: &str) -> impl Future<Output = bool> + Send;
    fn request_approval(&self, request: &ApprovalRequest) -> impl Future<Output = ()> + Send;
    /// The status once decided, or `"pending"` when time ran out.
    fn await_decision(&self, approval_id: &str) -> impl Future<Output = String> + Send;
    fn audit(&self, record: &AuditRecord) -> impl Future<Output = ()> + Send;
    fn expire(&self, approval_id: &str) -> impl Future<Output = ()> + Send;
}

/// Applies a plan. `false` means it failed; it never errors.
pub trait Remediator: Send {
    fn execute(&mut self, plan: &RemediationPlan) -> impl Future<Output = bool> + Send;
    fn rollback(&mut self, plan: &RemediationPlan) -> impl Future<Output = bool> + Send;
}

pub trait Health: Send + Sync {
    fn check(
        &self,
        situation: &Situation,
        target: &RemediationTarget,
    ) -> impl Future<Output = bool> + Send;
}

pub trait Rehearsal: Send + Sync {
    fn rehearse(
        &self,
        situation: &Situation,
        plan: &RemediationPlan,
    ) -> impl Future<Output = PreflightResult> + Send;
}

/// The deployment is the incident's own `service` (or `job`) label.
#[must_use]
pub fn resolve_target(situation: &Situation, namespace: &str) -> RemediationTarget {
    RemediationTarget {
        namespace: namespace.to_string(),
        deployment: service_of(situation).unwrap_or("unknown").to_string(),
    }
}

#[must_use]
pub fn service_of(situation: &Situation) -> Option<&str> {
    situation.member_events.iter().find_map(|event| {
        ["service", "job"].iter().find_map(|key| {
            event
                .labels
                .get(*key)
                .map(String::as_str)
                .filter(|v| !v.is_empty())
        })
    })
}

/// Anything unreadable counts as below every floor.
const UNSAFE: f64 = -1.0;

fn number(text: &str) -> f64 {
    match text.trim().parse::<f64>() {
        Ok(value) if !value.is_nan() => value,
        _ => UNSAFE,
    }
}

fn cpu_millicores(quantity: &str) -> f64 {
    let s = quantity.trim();
    match s.strip_suffix('m') {
        Some(millis) => number(millis),
        None => number(s) * 1000.0,
    }
}

fn mem_mebibytes(quantity: &str) -> f64 {
    const MIB: f64 = 1024.0 * 1024.0;
    let s = quantity.trim();
    let units: [(&str, f64); 6] = [
        ("Gi", 1024.0),
        ("Mi", 1.0),
        ("Ki", 1.0 / 1024.0),
        ("G", 1e9 / MIB),
        ("M", 1e6 / MIB),
        ("K", 1e3 / MIB),
    ];
    for (suffix, scale) in units {
        if let Some(value) = s.strip_suffix(suffix) {
            return number(value) * scale;
        }
    }
    number(s) / MIB
}

fn step_reason(step: &RemediationStep) -> Option<&'static str> {
    match step.action {
        // Dispatch would clamp this, but a playbook that intends to zero a
        // deployment is refused rather than silently softened.
        Action::Scale if step.replicas.is_some_and(|n| n <= SCALE_TAKEDOWN_DELTA) => {
            Some("denied:unsafe-scale")
        }
        Action::PatchResourceLimits => {
            let low_cpu = step
                .cpu_limit
                .as_deref()
                .is_some_and(|c| cpu_millicores(c) < MIN_CPU_MILLICORES);
            let low_mem = step
                .mem_limit
                .as_deref()
                .is_some_and(|m| mem_mebibytes(m) < MIN_MEM_MEBIBYTES);
            // A patch that changes nothing would report success misleadingly.
            let empty = step.cpu_limit.is_none() && step.mem_limit.is_none();
            (low_cpu || low_mem || empty).then_some("denied:unsafe-limits")
        }
        Action::PatchProbe => {
            let below = |value: Option<i64>, floor: i64| value.is_some_and(|v| v < floor);
            let unsafe_probe = step.probe.is_none()
                || below(step.failure_threshold, 1)
                || below(step.period_seconds, 1)
                || below(step.timeout_seconds, 1)
                || below(step.initial_delay_seconds, 0);
            unsafe_probe.then_some("denied:unsafe-probe")
        }
        Action::RollbackToRevision if step.revision.is_none() => Some("denied:unsafe-revision"),
        _ => None,
    }
}

/// Dangerous shapes of otherwise allowed actions.
#[must_use]
pub fn denylist_reason(playbook: &Playbook) -> Option<&'static str> {
    playbook
        .steps
        .iter()
        .chain(&playbook.rollback_steps)
        .find_map(step_reason)
}

#[must_use]
pub fn action_name(action: Action) -> &'static str {
    match action {
        Action::Restart => "restart",
        Action::Scale => "scale",
        Action::RollbackDeploy => "rollback_deploy",
        Action::Wait => "wait",
        Action::PatchResourceLimits => "patch_resource_limits",
        Action::RollbackToRevision => "rollback_to_revision",
        Action::PatchProbe => "patch_probe",
    }
}

#[must_use]
pub fn probe_field(probe: Option<ProbeKind>) -> &'static str {
    match probe {
        Some(ProbeKind::Liveness) => "livenessProbe",
        _ => "readinessProbe",
    }
}

fn describe(plan: &RemediationPlan) -> Vec<String> {
    plan.steps
        .iter()
        .map(|step| {
            let name = action_name(step.action);
            match (step.action, step.replicas, step.note.as_deref()) {
                (Action::Scale, Some(delta), _) => {
                    format!("scale {} {delta:+} replicas", plan.target.deployment)
                }
                (_, _, Some(note)) if !note.is_empty() => format!("{name}: {note}"),
                _ => name.to_string(),
            }
        })
        .collect()
}

pub struct Settings<'a> {
    pub namespace: &'a str,
    /// Reported on outcomes: `"dry_run"` or `"k8s"`.
    pub mode: &'a str,
}

fn outcome(
    situation: &Situation,
    playbook: &Playbook,
    result: RemediationResult,
    health_after: &str,
) -> RemediationOutcome {
    RemediationOutcome {
        situation_id: situation.id.clone(),
        playbook_id: playbook.id.clone(),
        result,
        health_after: health_after.to_string(),
        ts: Timestamp::now(),
        hitl_mode: playbook.hitl_mode,
        steps: Vec::new(),
        mode: "dry_run".to_string(),
        preflight: None,
        handling: Handling::Normal,
    }
}

async fn audit<G: Gate>(gate: &G, situation: &Situation, playbook: &Playbook, decision: &str) {
    gate.audit(&AuditRecord {
        actor: ACTOR.to_string(),
        action: "execute".to_string(),
        resource: format!("playbook:{}", playbook.id),
        decision: decision.to_string(),
        ts: Timestamp::now(),
        correlation_id: situation.id.clone(),
    })
    .await;
}

/// Run `playbook` for `situation`. `skip_approval` waives only the human
/// approval, and only the caller's evidence check may set it: every other gate
/// and the rehearsal apply unchanged.
#[allow(clippy::too_many_arguments)]
pub async fn execute<G: Gate, R: Remediator, H: Health, S: Rehearsal>(
    situation: &Situation,
    playbook: &Playbook,
    gate: &G,
    remediator: &mut R,
    health: &H,
    sandbox: &S,
    settings: &Settings<'_>,
    skip_approval: bool,
) -> RemediationOutcome {
    use RemediationResult::{Failure, RolledBack, Success};

    let refused = |reason: &str| outcome(situation, playbook, Failure, reason);

    if playbook.hitl_mode == HitlMode::Disabled {
        audit(gate, situation, playbook, "skipped").await;
        return refused("skipped:disabled");
    }
    if !playbook.reversible {
        audit(gate, situation, playbook, "refused").await;
        return refused("refused:not-reversible");
    }
    if !gate.allowed(&format!("playbook:{}", playbook.id)).await {
        audit(gate, situation, playbook, "deny").await;
        return refused("denied:rbac");
    }
    // Before the rehearsal: a dangerous step could still rehearse cleanly.
    if let Some(reason) = denylist_reason(playbook) {
        audit(gate, situation, playbook, "denied-unsafe").await;
        return refused(reason);
    }

    let plan = RemediationPlan {
        target: resolve_target(situation, settings.namespace),
        steps: playbook.steps.clone(),
        rollback_steps: playbook.rollback_steps.clone(),
    };
    let preflight = sandbox.rehearse(situation, &plan).await;
    let attempted = |result, reason: &str| {
        let mut done = outcome(situation, playbook, result, reason);
        done.steps = describe(&plan);
        done.mode = settings.mode.to_string();
        done.preflight = Some(preflight.clone());
        done
    };

    // With no human to advise, a failed rehearsal is a hard stop.
    if !preflight.passed && (playbook.hitl_mode == HitlMode::Auto || skip_approval) {
        audit(gate, situation, playbook, "preflight-failed").await;
        return attempted(Failure, "preflight-failed");
    }

    if playbook.hitl_mode == HitlMode::Hitl {
        if skip_approval {
            audit(gate, situation, playbook, "quiet-approved").await;
        } else {
            let request = ApprovalRequest {
                id: format!("appr-{}", situation.id),
                situation_id: situation.id.clone(),
                playbook_id: playbook.id.clone(),
                requested_by: ACTOR.to_string(),
                status: "pending".to_string(),
                decided_by: None,
                preflight: Some(preflight.clone()),
            };
            gate.request_approval(&request).await;
            let status = gate.await_decision(&request.id).await;
            if status != "approved" {
                let reason = if status == "rejected" {
                    "aborted:rejected"
                } else {
                    "aborted:timeout"
                };
                // Otherwise the console keeps offering a dead Approve button.
                if status == "pending" {
                    gate.expire(&request.id).await;
                }
                audit(gate, situation, playbook, "abort").await;
                let mut aborted = refused(reason);
                aborted.preflight = Some(preflight);
                return aborted;
            }
        }
    }

    if !remediator.execute(&plan).await {
        audit(gate, situation, playbook, "execute-failed").await;
        return attempted(Failure, "execute-failed");
    }
    if health.check(situation, &plan.target).await {
        audit(gate, situation, playbook, "allow").await;
        return attempted(Success, "healthy");
    }
    // Only report a rollback when something was actually undone.
    if plan.rollback_steps.is_empty() {
        audit(gate, situation, playbook, "unhealthy-no-rollback").await;
        return attempted(Failure, "unhealthy:no-rollback");
    }
    if !remediator.rollback(&plan).await {
        audit(gate, situation, playbook, "rollback-failed").await;
        return attempted(Failure, "unhealthy:rollback-failed");
    }
    audit(gate, situation, playbook, "rolled-back").await;
    attempted(RolledBack, "unhealthy:rolled-back")
}
