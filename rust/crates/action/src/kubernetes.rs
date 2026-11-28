//! Typed API calls only: no shell, no string commands, never a delete of a
//! workload. Any API error is a failed step.

use std::collections::HashMap;
use std::time::Duration;

use intelliops_contracts::{
    Action, RemediationPlan, RemediationStep, RemediationTarget, Situation, Timestamp,
};
use intelliops_detect::Policy;
use k8s_openapi::api::apps::v1::{Deployment, ReplicaSet};
use kube::api::{Api, ListParams, Patch, PatchParams};
use kube::{Client, ResourceExt};
use serde_json::{Map, Value, json};
use tokio::time::Instant;

use crate::gates::{Health, Remediator, probe_field, service_of};
use crate::verify::{firing_metrics, recovered};

const MIN_REPLICAS: i64 = 1;
const MAX_REPLICAS: i64 = 10;
const REVISION_ANNOTATION: &str = "deployment.kubernetes.io/revision";

#[derive(Debug)]
pub enum StepError {
    Api(kube::Error),
    Missing(String),
}

impl From<kube::Error> for StepError {
    fn from(error: kube::Error) -> Self {
        Self::Api(error)
    }
}

impl std::fmt::Display for StepError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Api(error) => write!(f, "{error}"),
            Self::Missing(what) => f.write_str(what),
        }
    }
}

fn restart_annotation(key: &str) -> Value {
    json!({"spec": {"template": {"metadata": {"annotations":
        {key: Timestamp::now().to_string()}}}}})
}

pub struct KubeRemediator {
    client: Client,
    /// Replicas before the first scale of a run, so rollback restores exactly
    /// that. Undoing by the inverse delta is wrong whenever scaling was clamped.
    original: HashMap<(String, String), i64>,
}

impl KubeRemediator {
    #[must_use]
    pub fn new(client: Client) -> Self {
        Self {
            client,
            original: HashMap::new(),
        }
    }

    fn deployments(&self, namespace: &str) -> Api<Deployment> {
        Api::namespaced(self.client.clone(), namespace)
    }

    async fn patch(&self, target: &RemediationTarget, body: Value) -> Result<(), StepError> {
        self.deployments(&target.namespace)
            .patch(
                &target.deployment,
                &PatchParams::default(),
                &Patch::Strategic(body),
            )
            .await?;
        Ok(())
    }

    /// Strategic merge keys containers by name, so "the first one" is looked up.
    async fn container(
        &self,
        target: &RemediationTarget,
        step: &RemediationStep,
    ) -> Result<String, StepError> {
        if let Some(name) = &step.container {
            return Ok(name.clone());
        }
        let deployment = self
            .deployments(&target.namespace)
            .get(&target.deployment)
            .await?;
        deployment
            .spec
            .and_then(|s| s.template.spec)
            .and_then(|s| s.containers.into_iter().next())
            .map(|c| c.name)
            .ok_or_else(|| StepError::Missing("deployment has no container".to_string()))
    }

    async fn scale(
        &mut self,
        target: &RemediationTarget,
        delta: i64,
        rolling_back: bool,
    ) -> Result<(), StepError> {
        let api = self.deployments(&target.namespace);
        let key = (target.namespace.clone(), target.deployment.clone());
        let desired = match self.original.get(&key) {
            Some(&original) if rolling_back => original,
            _ => {
                let current = api
                    .get(&target.deployment)
                    .await?
                    .spec
                    .and_then(|s| s.replicas)
                    .map_or(1, i64::from);
                if !rolling_back {
                    self.original.entry(key).or_insert(current);
                }
                current
                    .saturating_add(delta)
                    .clamp(MIN_REPLICAS, MAX_REPLICAS)
            }
        };
        api.patch_scale(
            &target.deployment,
            &PatchParams::default(),
            &Patch::Merge(json!({"spec": {"replicas": desired}})),
        )
        .await?;
        Ok(())
    }

    async fn rollback_to(
        &self,
        target: &RemediationTarget,
        revision: i64,
    ) -> Result<(), StepError> {
        let deployment = self
            .deployments(&target.namespace)
            .get(&target.deployment)
            .await?;
        let selector = deployment
            .spec
            .and_then(|s| s.selector.match_labels)
            .unwrap_or_default()
            .iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join(",");
        let replica_sets: Api<ReplicaSet> = Api::namespaced(self.client.clone(), &target.namespace);
        let wanted = revision.to_string();
        let found = replica_sets
            .list(&ListParams::default().labels(&selector))
            .await?
            .items
            .into_iter()
            .find(|rs| {
                let owned = rs
                    .owner_references()
                    .iter()
                    .any(|o| o.kind == "Deployment" && o.name == target.deployment);
                owned && rs.annotations().get(REVISION_ANNOTATION) == Some(&wanted)
            });
        let mut template = found
            .and_then(|rs| rs.spec)
            .and_then(|s| s.template)
            .ok_or_else(|| {
                StepError::Missing(format!(
                    "revision {revision} not found for {}",
                    target.deployment
                ))
            })?;
        // The hash belongs to the old ReplicaSet, as `kubectl rollout undo` knows.
        if let Some(labels) = template.metadata.as_mut().and_then(|m| m.labels.as_mut()) {
            labels.remove("pod-template-hash");
        }
        self.patch(target, json!({"spec": {"template": template}}))
            .await
    }

    async fn apply(
        &mut self,
        target: &RemediationTarget,
        step: &RemediationStep,
        rolling_back: bool,
    ) -> Result<(), StepError> {
        match step.action {
            // Readiness is the health check's job.
            Action::Wait => Ok(()),
            Action::Restart => {
                let body = restart_annotation("kubectl.kubernetes.io/restartedAt");
                self.patch(target, body).await
            }
            // The demo's fault lives in memory, so recreating pods undoes it.
            Action::RollbackDeploy => {
                let body = restart_annotation("intelliops.io/rolledBackAt");
                self.patch(target, body).await
            }
            Action::Scale => {
                self.scale(target, step.replicas.unwrap_or(0), rolling_back)
                    .await
            }
            Action::PatchResourceLimits => {
                let mut limits = Map::new();
                if let Some(cpu) = &step.cpu_limit {
                    limits.insert("cpu".into(), cpu.clone().into());
                }
                if let Some(memory) = &step.mem_limit {
                    limits.insert("memory".into(), memory.clone().into());
                }
                let name = self.container(target, step).await?;
                let container = json!({"name": name, "resources": {"limits": limits}});
                self.patch(
                    target,
                    json!({"spec": {"template": {"spec": {"containers": [container]}}}}),
                )
                .await
            }
            Action::PatchProbe => {
                let mut probe = Map::new();
                for (key, value) in [
                    ("initialDelaySeconds", step.initial_delay_seconds),
                    ("periodSeconds", step.period_seconds),
                    ("timeoutSeconds", step.timeout_seconds),
                    ("failureThreshold", step.failure_threshold),
                ] {
                    if let Some(value) = value {
                        probe.insert(key.into(), value.into());
                    }
                }
                let name = self.container(target, step).await?;
                let mut container = Map::new();
                container.insert("name".into(), name.into());
                container.insert(probe_field(step.probe).into(), probe.into());
                self.patch(
                    target,
                    json!({"spec": {"template": {"spec": {"containers": [container]}}}}),
                )
                .await
            }
            Action::RollbackToRevision => match step.revision {
                Some(revision) => self.rollback_to(target, revision).await,
                None => Err(StepError::Missing("no revision given".to_string())),
            },
        }
    }

    async fn run(
        &mut self,
        target: &RemediationTarget,
        steps: &[RemediationStep],
        rolling_back: bool,
    ) -> bool {
        for step in steps {
            if let Err(error) = self.apply(target, step, rolling_back).await {
                tracing::warn!(%error, namespace = %target.namespace,
                    deployment = %target.deployment, "remediation step failed");
                return false;
            }
        }
        true
    }

    fn forget(&mut self, target: &RemediationTarget) {
        self.original
            .remove(&(target.namespace.clone(), target.deployment.clone()));
    }
}

impl Remediator for KubeRemediator {
    async fn execute(&mut self, plan: &RemediationPlan) -> bool {
        self.forget(&plan.target);
        self.run(&plan.target, &plan.steps, false).await
    }

    async fn rollback(&mut self, plan: &RemediationPlan) -> bool {
        let ok = self.run(&plan.target, &plan.rollback_steps, true).await;
        self.forget(&plan.target);
        ok
    }
}

/// Whether the rollout has converged: the controller has seen the new spec,
/// every replica is updated, and every one is ready.
#[must_use]
pub fn converged(deployment: &Deployment) -> bool {
    let Some(status) = &deployment.status else {
        return false;
    };
    // Right after a patch the status still describes the old, healthy pods.
    if let (Some(generation), Some(observed)) =
        (deployment.metadata.generation, status.observed_generation)
        && observed < generation
    {
        return false;
    }
    let desired = status.replicas.unwrap_or(0);
    if status.updated_replicas.is_some_and(|u| u < desired) {
        return false;
    }
    desired > 0 && status.ready_replicas.unwrap_or(0) == desired
}

/// Re-reads a metric's current value, scoped to the incident's service.
#[derive(Clone)]
pub struct MetricSource {
    pub client: reqwest::Client,
    pub prometheus_url: String,
    pub policy: Policy,
    pub z_threshold: f64,
}

impl MetricSource {
    /// The worst instance, because all of them must have recovered.
    async fn current(&self, metric: &str, service: Option<&str>) -> Option<f64> {
        let query = match service {
            Some(service) => format!("{metric}{{service=\"{service}\"}}"),
            None => metric.to_string(),
        };
        let url = format!("{}/api/v1/query", self.prometheus_url);
        let body: Value = self
            .client
            .get(url)
            .query(&[("query", query)])
            .timeout(Duration::from_secs(5))
            .send()
            .await
            .ok()?
            .json()
            .await
            .ok()?;
        body["data"]["result"]
            .as_array()?
            .iter()
            .map(|r| r["value"][1].as_str()?.parse::<f64>().ok())
            .collect::<Option<Vec<_>>>()?
            .into_iter()
            .reduce(f64::max)
    }

    pub async fn all_recovered(&self, situation: &Situation, service: Option<&str>) -> bool {
        for metric in firing_metrics(situation) {
            let current = self.current(metric, service).await;
            if !recovered(situation, metric, current, self.policy, self.z_threshold) {
                return false;
            }
        }
        true
    }
}

/// Healthy means converged pods and, when there is a metric source, recovered
/// metrics. An unreachable cluster times out to unhealthy.
pub struct KubeHealth {
    pub client: Client,
    pub metrics: Option<MetricSource>,
    pub timeout: Duration,
    pub poll: Duration,
}

impl KubeHealth {
    async fn ready(&self, target: &RemediationTarget) -> bool {
        let api: Api<Deployment> = Api::namespaced(self.client.clone(), &target.namespace);
        match api.get(&target.deployment).await {
            Ok(deployment) => converged(&deployment),
            Err(error) => {
                tracing::debug!(%error, "deployment status unavailable");
                false
            }
        }
    }
}

impl Health for KubeHealth {
    async fn check(&self, situation: &Situation, target: &RemediationTarget) -> bool {
        let deadline = Instant::now() + self.timeout;
        loop {
            if self.ready(target).await {
                let recovered = match &self.metrics {
                    Some(metrics) => {
                        metrics
                            .all_recovered(situation, service_of(situation))
                            .await
                    }
                    None => true,
                };
                if recovered {
                    return true;
                }
            }
            if Instant::now() >= deadline {
                return false;
            }
            tokio::time::sleep(self.poll).await;
        }
    }
}
