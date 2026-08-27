import * as api from "./api";
import * as mock from "./mock";
import type { LlmProbe, SystemInfo } from "./types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

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
