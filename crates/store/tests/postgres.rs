//! Every operation against both backends. Postgres runs when
//! `INTELLIOPS_TEST_DATABASE_URL` points at a disposable database.

#![allow(clippy::unwrap_used)]

use intelliops_contracts::*;
use intelliops_store::{BaselineRow, Store};

fn unique(prefix: &str) -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    format!("{prefix}-{nanos}")
}

async fn exercise(store: Store) {
    store.migrate().await.unwrap();
    store.migrate().await.unwrap();
    store.ping().await.unwrap();
    let id = unique("t");
    let now = Timestamp::parse("2026-01-02T03:04:05.000006Z").unwrap();

    let audit = AuditRecord {
        actor: "a".into(),
        action: "approve".into(),
        resource: "playbook:x".into(),
        decision: "allow".into(),
        ts: now,
        correlation_id: id.clone(),
    };
    store.audit_write(&audit).await.unwrap();
    assert_eq!(store.audit_records(Some(&id)).await.unwrap(), [audit]);
    assert!(!store.audit_records(None).await.unwrap().is_empty());

    let training = TrainingRecord {
        situation_id: id.clone(),
        signature: id.clone(),
        playbook_id: "p".into(),
        result: RemediationResult::RolledBack,
        worked: false,
        ts: now,
        mode: Some("k8s".into()),
    };
    store.training_append(&training).await.unwrap();
    assert!(store.training_all().await.unwrap().contains(&training));

    let mut playbook = Playbook {
        id: id.clone(),
        name: "n".into(),
        match_rule: "m".into(),
        steps: Vec::new(),
        hitl_mode: HitlMode::Hitl,
        reversible: true,
        rollback_steps: Vec::new(),
        symptoms: None,
    };
    store
        .playbook_seed(std::slice::from_ref(&playbook))
        .await
        .unwrap();
    playbook.hitl_mode = HitlMode::Auto;
    store.playbook_register(&playbook).await.unwrap();
    // Seeding again must not undo the graduation.
    let mut seed = playbook.clone();
    seed.hitl_mode = HitlMode::Hitl;
    store.playbook_seed(&[seed]).await.unwrap();
    assert_eq!(
        store.playbook_get(&id).await.unwrap(),
        Some(playbook.clone())
    );
    assert!(store.playbook_list().await.unwrap().contains(&playbook));

    let approval = ApprovalRequest {
        id: id.clone(),
        situation_id: "s".into(),
        playbook_id: "p".into(),
        requested_by: "action-service".into(),
        status: "pending".into(),
        decided_by: None,
        preflight: None,
    };
    store.approval_put(&approval).await.unwrap();
    assert!(store.approvals_pending().await.unwrap().contains(&approval));
    let decided = store
        .approval_decide(&id, "approved", "alice")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(decided.status, "approved");
    assert_eq!(decided.decided_by.as_deref(), Some("alice"));
    assert_eq!(
        store.approval_decide(&id, "rejected", "bob").await.unwrap(),
        None
    );
    assert_eq!(store.approval_get(&id).await.unwrap(), Some(decided));

    let proposal = ProposedPlaybook {
        id: id.clone(),
        playbook: playbook.clone(),
        status: ProposalStatus::Proposed,
        proposed_by: "ai".into(),
        rationale: None,
        source_situation_id: None,
        decided_by: None,
        ts: now,
    };
    store.proposal_add(&proposal).await.unwrap();
    let open = store
        .proposals(Some(ProposalStatus::Proposed))
        .await
        .unwrap();
    assert!(open.contains(&proposal));
    let approved = store
        .proposal_decide(&id, ProposalStatus::Approved, "alice")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(approved.status, ProposalStatus::Approved);
    assert_eq!(store.proposal_get(&id).await.unwrap(), Some(approved));

    let decision = AuthorDecision {
        signature: id.clone(),
        proposal_id: id.clone(),
        playbook_id: id.clone(),
        actions: vec!["restart".into()],
        cited_facts: Vec::new(),
        note: None,
        disposition: Disposition::Pending,
        outcome: DecisionOutcome::Unknown,
        decided_by: None,
        ts: now,
    };
    store.decision_record(&decision).await.unwrap();
    store
        .decision_disposition(&id, Disposition::Accepted, "alice")
        .await
        .unwrap();
    store
        .decision_outcome(&id, DecisionOutcome::Worked)
        .await
        .unwrap();
    let stored = store.decisions_by_signature(&id).await.unwrap();
    assert_eq!(stored[0].disposition, Disposition::Accepted);
    assert_eq!(stored[0].outcome, DecisionOutcome::Worked);

    store.run_start(&id, "sig", now).await.unwrap();
    let step = TraceStep {
        run_id: id.clone(),
        seq: 0,
        kind: TraceStepKind::ToolCall,
        ts: now,
        text: None,
        tool: Some("t".into()),
        arguments: None,
        result_summary: None,
        detail: None,
    };
    store.run_step(&step).await.unwrap();
    store
        .run_finish(&id, "succeeded", Some("p1"), now)
        .await
        .unwrap();
    assert_eq!(store.run_steps(&id).await.unwrap(), [step]);
    let runs = store.recent_runs(500).await.unwrap();
    let run = runs.iter().find(|r| r.run_id == id).unwrap();
    assert_eq!((run.step_count, run.status.as_str()), (1, "succeeded"));
    assert_eq!(run.proposal_id.as_deref(), Some("p1"));

    let row = BaselineRow {
        metric_name: id.clone(),
        n: 3.0,
        mean: 1.5,
        variance: 0.25,
        count: 3,
    };
    store
        .baseline_save(std::slice::from_ref(&row))
        .await
        .unwrap();
    store
        .baseline_save(std::slice::from_ref(&row))
        .await
        .unwrap();
    assert!(store.baseline_load().await.unwrap().contains(&row));
    store.model_save(&id, b"abc").await.unwrap();
    assert_eq!(store.model_load(&id).await.unwrap(), Some(b"abc".to_vec()));
}

#[tokio::test]
async fn memory_backend() {
    exercise(Store::memory()).await;
}

#[tokio::test]
async fn postgres_backend() {
    let Ok(url) = std::env::var("INTELLIOPS_TEST_DATABASE_URL") else {
        eprintln!("skipped: INTELLIOPS_TEST_DATABASE_URL is not set");
        return;
    };
    exercise(Store::postgres(&url).unwrap()).await;
}
