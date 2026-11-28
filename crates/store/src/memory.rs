use std::collections::BTreeMap;

use intelliops_contracts::{
    ApprovalRequest, AuditRecord, AuthorDecision, Playbook, ProposedPlaybook, RunSummary,
    TraceStep, TrainingRecord,
};

use crate::correlation::BaselineRow;

#[derive(Debug, Default)]
pub struct Memory {
    pub(crate) audit: Vec<AuditRecord>,
    pub(crate) training: Vec<TrainingRecord>,
    pub(crate) playbooks: BTreeMap<String, Playbook>,
    /// Oldest update first.
    pub(crate) approvals: Vec<ApprovalRequest>,
    pub(crate) baseline: BTreeMap<String, BaselineRow>,
    pub(crate) models: BTreeMap<String, Vec<u8>>,
    pub(crate) decisions: Vec<AuthorDecision>,
    pub(crate) runs: Vec<RunSummary>,
    pub(crate) steps: Vec<TraceStep>,
    pub(crate) proposals: Vec<ProposedPlaybook>,
}
