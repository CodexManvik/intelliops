import * as api from "./api";
import * as mock from "./mock";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

export const loadSituations = LIVE
  ? api.loadSituations
  : async () => mock.situations;
export const loadOutcomes = LIVE ? api.loadOutcomes : async () => mock.outcomes;
export const loadAudit = LIVE ? api.loadAudit : async () => mock.audit;
export const loadPlaybooks = LIVE ? api.loadPlaybooks : async () => mock.playbooks;
export const decideApproval = LIVE
  ? api.decideApproval
  : async () => {
      /* mock mode: no-op; Incidents' local optimistic update drives the UI */
    };
