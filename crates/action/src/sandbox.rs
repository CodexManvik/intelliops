//! Rehearse a plan on a clone in a throwaway namespace before anyone approves
//! it. The clone's pod health is the signal. Prometheus does not scrape the
//! clone, so a metric check there can only fail safe. The namespace is always
//! deleted.

use std::collections::BTreeSet;
use std::time::Duration;

use intelliops_contracts::{PreflightResult, RemediationPlan, RemediationTarget, Situation};
use k8s_openapi::api::apps::v1::{Deployment, ReplicaSet};
use k8s_openapi::api::core::v1::{ConfigMap, Namespace, PodSpec, Service};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{ObjectMeta, OwnerReference};
use kube::api::{Api, DeleteParams, ListParams, PostParams};
use kube::{Client, ResourceExt};
use serde_json::json;

use crate::gates::{Health, Rehearsal, Remediator};
use crate::kubernetes::{KubeHealth, KubeRemediator, StepError};

const ROLLOUT_TIMEOUT: Duration = Duration::from_secs(60);
const POLL: Duration = Duration::from_secs(2);

/// Drop what the server assigned, which a create must not carry.
fn detach(meta: &mut ObjectMeta, namespace: &str) {
    meta.namespace = Some(namespace.to_string());
    meta.resource_version = None;
    meta.uid = None;
    meta.creation_timestamp = None;
    meta.owner_references = None;
    meta.managed_fields = None;
    meta.self_link = None;
    if let Some(annotations) = &mut meta.annotations {
        annotations.remove("deployment.kubernetes.io/revision");
        annotations.remove("kubectl.kubernetes.io/last-applied-configuration");
    }
}

fn config_maps(spec: &PodSpec) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    for volume in spec.volumes.iter().flatten() {
        if let Some(map) = &volume.config_map {
            names.insert(map.name.clone());
        }
    }
    for container in &spec.containers {
        for source in container.env_from.iter().flatten() {
            if let Some(reference) = &source.config_map_ref {
                names.insert(reference.name.clone());
            }
        }
        for env in container.env.iter().flatten() {
            if let Some(reference) = env
                .value_from
                .as_ref()
                .and_then(|v| v.config_map_key_ref.as_ref())
            {
                names.insert(reference.name.clone());
            }
        }
    }
    names
}

pub struct CloneSandbox {
    pub client: Client,
    /// Where the real workload lives.
    pub namespace: String,
}

impl CloneSandbox {
    /// Copy the Service and ConfigMaps if they exist. Nice to have only.
    async fn copy_extras(&self, deployment: &Deployment, name: &str, sandbox: &str) {
        let source: Api<Service> = Api::namespaced(self.client.clone(), &self.namespace);
        if let Ok(mut service) = source.get(name).await {
            detach(&mut service.metadata, sandbox);
            service.status = None;
            if let Some(spec) = &mut service.spec {
                spec.cluster_ip = None;
                spec.cluster_ips = None;
                for port in spec.ports.iter_mut().flatten() {
                    port.node_port = None;
                }
            }
            let target: Api<Service> = Api::namespaced(self.client.clone(), sandbox);
            if let Err(error) = target.create(&PostParams::default(), &service).await {
                tracing::debug!(%error, "service not cloned");
            }
        }
        let Some(spec) = deployment
            .spec
            .as_ref()
            .and_then(|s| s.template.spec.as_ref())
        else {
            return;
        };
        let source: Api<ConfigMap> = Api::namespaced(self.client.clone(), &self.namespace);
        let target: Api<ConfigMap> = Api::namespaced(self.client.clone(), sandbox);
        for map_name in config_maps(spec) {
            if let Ok(mut map) = source.get(&map_name).await {
                detach(&mut map.metadata, sandbox);
                if let Err(error) = target.create(&PostParams::default(), &map).await {
                    tracing::debug!(%error, "config map not cloned");
                }
            }
        }
    }

    /// Copy revision history so a rollback to a revision can be rehearsed.
    async fn copy_history(&self, deployment: &Deployment, uid: &str, sandbox: &str) {
        let name = deployment.name_any();
        let selector = deployment
            .spec
            .as_ref()
            .and_then(|s| s.selector.match_labels.as_ref())
            .map(|labels| {
                labels
                    .iter()
                    .map(|(k, v)| format!("{k}={v}"))
                    .collect::<Vec<_>>()
                    .join(",")
            })
            .unwrap_or_default();
        let source: Api<ReplicaSet> = Api::namespaced(self.client.clone(), &self.namespace);
        let Ok(sets) = source.list(&ListParams::default().labels(&selector)).await else {
            return;
        };
        let target: Api<ReplicaSet> = Api::namespaced(self.client.clone(), sandbox);
        for mut set in sets.items {
            let owned = set
                .owner_references()
                .iter()
                .any(|o| o.kind == "Deployment" && o.name == name);
            if !owned || uid.is_empty() {
                continue;
            }
            let revision = set
                .annotations()
                .get("deployment.kubernetes.io/revision")
                .cloned();
            detach(&mut set.metadata, sandbox);
            if let Some(revision) = revision {
                set.annotations_mut()
                    .insert("deployment.kubernetes.io/revision".into(), revision);
            }
            set.metadata.owner_references = Some(vec![OwnerReference {
                api_version: "apps/v1".into(),
                kind: "Deployment".into(),
                name: name.clone(),
                uid: uid.to_string(),
                controller: Some(true),
                block_owner_deletion: Some(true),
            }]);
            set.status = None;
            if let Err(error) = target.create(&PostParams::default(), &set).await {
                tracing::debug!(%error, "revision not cloned");
            }
        }
    }

    async fn run(
        &self,
        situation: &Situation,
        plan: &RemediationPlan,
        sandbox: &str,
    ) -> Result<(bool, String), StepError> {
        let name = &plan.target.deployment;
        let source: Api<Deployment> = Api::namespaced(self.client.clone(), &self.namespace);
        let original = source.get(name).await?;

        let namespace: Namespace = serde_json::from_value(json!({
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": sandbox, "labels": {
                "intelliops.io/sandbox": "true",
                "app.kubernetes.io/managed-by": "intelliops",
            }},
        }))
        .map_err(|e| StepError::Missing(e.to_string()))?;
        Api::<Namespace>::all(self.client.clone())
            .create(&PostParams::default(), &namespace)
            .await?;

        let mut clone = original.clone();
        detach(&mut clone.metadata, sandbox);
        clone.status = None;
        let target: Api<Deployment> = Api::namespaced(self.client.clone(), sandbox);
        let created = target.create(&PostParams::default(), &clone).await?;
        self.copy_history(&original, created.uid().as_deref().unwrap_or(""), sandbox)
            .await;
        self.copy_extras(&original, name, sandbox).await;

        let clone_target = RemediationTarget {
            namespace: sandbox.to_string(),
            deployment: name.clone(),
        };
        let health = KubeHealth {
            client: self.client.clone(),
            metrics: None,
            timeout: ROLLOUT_TIMEOUT,
            poll: POLL,
        };
        if !health.check(situation, &clone_target).await {
            return Ok((
                false,
                format!("sandbox: clone {name} never became ready before fix"),
            ));
        }
        let clone_plan = RemediationPlan {
            target: clone_target.clone(),
            ..plan.clone()
        };
        if !KubeRemediator::new(self.client.clone())
            .execute(&clone_plan)
            .await
        {
            return Ok((
                false,
                format!("sandbox: clone {name} remediation apply failed"),
            ));
        }
        Ok(if health.check(situation, &clone_target).await {
            (true, format!("sandbox: clone {name} healthy after fix"))
        } else {
            (
                false,
                format!("sandbox: clone {name} did not recover after fix"),
            )
        })
    }
}

impl Rehearsal for CloneSandbox {
    async fn rehearse(&self, situation: &Situation, plan: &RemediationPlan) -> PreflightResult {
        let mut suffix = uuid::Uuid::new_v4().simple().to_string();
        suffix.truncate(8);
        let sandbox = format!("intelliops-sandbox-{suffix}");
        let (passed, detail) = match self.run(situation, plan, &sandbox).await {
            Ok(verdict) => verdict,
            Err(error) => {
                tracing::warn!(%error, "sandbox rehearsal failed");
                (false, "sandbox error".to_string())
            }
        };
        let namespaces: Api<Namespace> = Api::all(self.client.clone());
        if let Err(error) = namespaces.delete(&sandbox, &DeleteParams::default()).await {
            tracing::warn!(%error, sandbox, "sandbox teardown failed");
        }
        PreflightResult {
            passed,
            detail,
            mode: "k8s".to_string(),
            sandbox_namespace: Some(sandbox),
        }
    }
}
