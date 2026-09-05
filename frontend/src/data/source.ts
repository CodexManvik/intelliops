import * as api from "./api";
import * as mock from "./mock";
import type { LlmProbe, ProposedPlaybook, Situation, SystemInfo } from "./types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

// mock mode: proposals mutate this module-local copy so approve/reject and a
// freshly-drafted proposal are reflected back when the queue reloads, without
// a server. Reset on page load, same lifespan as the rest of the mock store.
const _mockProposals: ProposedPlaybook[] = [...mock.proposals];

export const loadSituations = LIVE
  ? api.loadSituations
  : async () => mock.situations;
export const loadSituationDetail = LIVE
  ? api.loadSituationDetail
  : async (id: string) => mock.situations.find((s) => s.id === id) ?? mock.situations[0];
export const loadSystem = LIVE
  ? api.loadSystem
  : async () => mock.system;
export const loadOutcomes = LIVE ? api.loadOutcomes : async () => mock.outcomes;
export const loadAudit = LIVE ? api.loadAudit : async () => mock.audit;
export const loadPlaybooks = LIVE ? api.loadPlaybooks : async () => mock.playbooks;
export const loadMetrics = LIVE ? api.loadMetrics : async () => mock.metrics;
export const decideApproval = LIVE
  ? api.decideApproval
  : async () => {
      /* mock mode: no-op; Incidents' local optimistic update drives the UI */
    };
export const loadBaseline = LIVE ? api.loadBaseline : async () => mock.baseline;
export const loadLlmConfig = LIVE ? api.loadLlmConfig : async () => mock.system.llm;
export const setLlmConfig = LIVE
  ? api.setLlmConfig
  : async (_cfg: { endpoint: string; api_key: string; model: string }): Promise<SystemInfo["llm"]> =>
      mock.system.llm;
export const testLlmConfig = LIVE
  ? api.testLlmConfig
  : async (_cfg: { endpoint: string; api_key: string; model: string }): Promise<LlmProbe> => ({
      ok: false,
      error: "mock mode",
    });

export const loadProposals = LIVE ? api.loadProposals : async () => _mockProposals;

export const proposePlaybook = LIVE
  ? api.proposePlaybook
  : async (situation: Situation, _requestedBy: string): Promise<ProposedPlaybook> => {
      // mock mode: fabricate a proposal the same shape the server would return
      // (server-assigned id, forced hitl) — honest about being a stub draft.
      const proposal: ProposedPlaybook = {
        id: `prop-mock-${Date.now().toString(36)}`,
        playbook: {
          id: `ai-${situation.signature}-mock`,
          name: `Drafted fix · ${situation.service}`,
          match_rule: situation.id,
          steps: [{ action: "restart", note: "mock draft — no LLM configured" }],
          hitl_mode: "hitl",
          reversible: true,
          rollback_steps: [],
        },
        status: "proposed",
        proposed_by: "runbook-author",
        rationale: "mock mode: no LLM configured — this is a stub draft, not a real AI proposal.",
        source_situation_id: situation.id,
        decided_by: null,
        ts: Date.now(),
      };
      _mockProposals.push(proposal);
      return proposal;
    };

export const approveProposal = LIVE
  ? api.approveProposal
  : async (id: string, decidedBy: string): Promise<ProposedPlaybook> => {
      const p = _mockProposals.find((x) => x.id === id);
      if (!p) throw new Error(`proposal ${id} not found`);
      p.status = "approved";
      p.decided_by = decidedBy;
      return p;
    };

export const rejectProposal = LIVE
  ? api.rejectProposal
  : async (id: string, decidedBy: string): Promise<ProposedPlaybook> => {
      const p = _mockProposals.find((x) => x.id === id);
      if (!p) throw new Error(`proposal ${id} not found`);
      p.status = "rejected";
      p.decided_by = decidedBy;
      return p;
    };
