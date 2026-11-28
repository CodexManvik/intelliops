//! Every gate decides as the Python gates did, in the same order of effects.

#![allow(clippy::unwrap_used)]

use std::sync::{Arc, Mutex};

use intelliops_action::gates::Settings;
use intelliops_action::verify::{firing_metrics, recovered};
use intelliops_action::{Gate, Health, Rehearsal, Remediator, execute};
use intelliops_contracts::*;
use intelliops_detect::{Policy, Thresholds};
use serde_json::Value;

type Calls = Arc<Mutex<Vec<String>>>;

#[derive(Clone)]
struct World {
    calls: Calls,
    spec: Value,
}

impl World {
    fn call(&self, name: impl Into<String>) {
        self.calls.lock().unwrap().push(name.into());
    }

    fn flag(&self, key: &str) -> bool {
        self.spec[key].as_bool().unwrap()
    }
}

impl Gate for World {
    async fn allowed(&self, _resource: &str) -> bool {
        self.flag("rbac")
    }

    async fn request_approval(&self, _request: &ApprovalRequest) {
        self.call("request");
    }

    async fn await_decision(&self, _approval_id: &str) -> String {
        self.spec["decision"].as_str().unwrap().to_string()
    }

    async fn audit(&self, record: &AuditRecord) {
        self.call(format!("audit:{}", record.decision));
    }

    async fn expire(&self, _approval_id: &str) {
        self.call("expire");
    }
}

impl Remediator for World {
    async fn execute(&mut self, plan: &RemediationPlan) -> bool {
        self.call(format!("execute:{}", plan.target.deployment));
        self.flag("execute")
    }

    async fn rollback(&mut self, _plan: &RemediationPlan) -> bool {
        self.call("rollback");
        self.flag("rollback")
    }
}

impl Health for World {
    async fn check(&self, _: &Situation, _: &RemediationTarget) -> bool {
        self.flag("healthy")
    }
}

impl Rehearsal for World {
    async fn rehearse(&self, _: &Situation, _: &RemediationPlan) -> PreflightResult {
        self.call("rehearse");
        PreflightResult {
            passed: self.flag("preflight"),
            detail: "d".into(),
            mode: "off".into(),
            sandbox_namespace: None,
        }
    }
}

fn recording() -> Value {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../testdata/action.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[tokio::test]
async fn the_gates_match_python() {
    let cases = recording()["gates"].as_array().unwrap().clone();
    assert!(cases.len() >= 2500);
    for (index, case) in cases.iter().enumerate() {
        let situation: Situation = serde_json::from_value(case["situation"].clone()).unwrap();
        let playbook: Playbook = serde_json::from_value(case["playbook"].clone()).unwrap();
        let world = World {
            calls: Calls::default(),
            spec: case["world"].clone(),
        };
        let out = execute(
            &situation,
            &playbook,
            &world,
            &mut world.clone(),
            &world,
            &world,
            &Settings {
                namespace: "intelliops-demo",
                mode: "dry_run",
            },
            world.flag("skip_approval"),
        )
        .await;
        let mut ours = serde_json::to_value(&out).unwrap();
        ours.as_object_mut().unwrap().remove("ts");
        assert_eq!(ours, case["outcome"], "case {index}");
        let calls = world.calls.lock().unwrap().clone();
        let expected: Vec<String> = serde_json::from_value(case["calls"].clone()).unwrap();
        assert_eq!(calls, expected, "case {index}");
    }
}

#[test]
fn recovery_is_judged_as_python_judged_it() {
    let cases = recording()["verify"].as_array().unwrap().clone();
    assert!(cases.len() >= 600);
    for (index, case) in cases.iter().enumerate() {
        let situation: Situation = serde_json::from_value(case["situation"].clone()).unwrap();
        let policy = if case["policy"] == true {
            Policy::On(Thresholds::default())
        } else {
            Policy::Off
        };
        let healthy = firing_metrics(&situation).iter().all(|metric| {
            let current = case["values"][*metric].as_f64();
            recovered(&situation, metric, current, policy, 3.0)
        });
        assert_eq!(Value::Bool(healthy), case["healthy"], "case {index}");
    }
}
