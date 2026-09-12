import * as api from "./api";
import * as mock from "./mock";
import type { LlmProbe, ProposedPlaybook, RunSummary, Situation, SystemInfo, TraceStep } from "./types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

// mock mode: proposals mutate this module-local copy so approve/reject and a
// freshly-drafted proposal are reflected back when the queue reloads, without
// a server. Reset on page load, same lifespan as the rest of the mock store.
const _mockProposals: ProposedPlaybook[] = [...mock.proposals];

// mock mode: agent runs/steps, seeded from mock.ts and appended to by the
// mock draftAsync below (see draftAsync) so a freshly-triggered draft shows
// up immediately in the Agent Activity feed without a server.
const _mockRuns: RunSummary[] = [...mock.agentRunSummaries];
const _mockSteps: Record<string, TraceStep[]> = { ...mock.agentRunSteps };

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

/* ---------------------------------------------------------------------------
   Agent Activity — AI runbook author trace (Task 7)
--------------------------------------------------------------------------- */

export const draftAsync = LIVE
  ? api.draftAsync
  : async (situation: Situation, _requestedBy: string): Promise<{ run_id: string }> => {
      // mock mode: fabricate a run the same shape draft-async would start,
      // already terminal (mock has no LLM/agent loop to run live) — the UI
      // then loads its stored steps rather than streaming.
      const run_id = `run-mock-${Date.now().toString(36)}`;
      _mockRuns.unshift({
        run_id,
        started_at: Date.now(),
        status: "succeeded",
        signature: situation.signature,
        step_count: mock.agentRunSteps["run-mock0001"].length,
        proposal_id: "prop-demo0001",
      });
      _mockSteps[run_id] = mock.agentRunSteps["run-mock0001"].map((s) => ({ ...s, run_id }));
      return { run_id };
    };

export const loadAgentRuns = LIVE ? api.loadAgentRuns : async (): Promise<RunSummary[]> => _mockRuns;

export const loadAgentRun = LIVE
  ? api.loadAgentRun
  : async (runId: string): Promise<TraceStep[]> => _mockSteps[runId] ?? [];

// mock mode: no server to stream from — every mock run is already terminal
// (see draftAsync above), so AgentActivity should never need to open a
// stream there. This stub exists so the same call-site works if it ever is
// called in mock mode: an EventSource-shaped object that immediately closes
// and never fires — the view falls back to loadAgentRun for stored steps.
export const openAgentRunStream = LIVE
  ? api.openAgentRunStream
  : (_runId: string): EventSource => {
      const target = new EventTarget();
      const stub = Object.assign(target, {
        readyState: 2, // CLOSED
        url: "",
        withCredentials: false,
        CONNECTING: 0 as const,
        OPEN: 1 as const,
        CLOSED: 2 as const,
        onopen: null,
        onmessage: null,
        onerror: null,
        close: () => {},
      });
      return stub as unknown as EventSource;
    };
