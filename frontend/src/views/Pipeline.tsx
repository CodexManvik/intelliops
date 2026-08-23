import { useEffect, useMemo, useState } from "react";
import { LayoutGroup, AnimatePresence } from "framer-motion";
import { motion, springSoft, SevChip, StatusChip, timeAgo, CTA } from "../components/primitives";
import { useLiveData } from "../hooks/useLiveData";
import { loadSituations, decideApproval } from "../data/source";
import { Reveal as Section } from "../hooks/useReveal";
import type { Situation } from "../data/types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

const LANES = ["detected", "diagnosed", "gate", "acting", "resolved"] as const;
type Lane = (typeof LANES)[number];
const LANE_LABEL: Record<Lane, string> = {
  detected: "Detected",
  diagnosed: "Diagnosed",
  gate: "Gate · HITL",
  acting: "Acting",
  resolved: "Resolved",
};

function laneOf(s: Situation): Lane {
  if (s.status === "resolved") return "resolved";
  if (s.status === "failed") return "gate";
  if (s.status === "acting") return s.hitl_mode === "hitl" ? "gate" : "acting";
  if (s.status === "diagnosed") return "diagnosed";
  return "detected";
}

const prefersReduced =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Mock-mode-only scripted journey: a small set of situations advances through
 * detected → diagnosed → acting → resolved on a timer, purely local to this
 * view (does not touch `data/mock.ts`'s `situations` export, which Incidents
 * depends on).
 */
const SCRIPT_STEPS: Array<{ id: string; status: Situation["status"] }[]> = [];
{
  const journey: Situation["status"][] = ["detected", "diagnosed", "acting", "resolved"];
  const ids = ["sit-pipe-a1", "sit-pipe-b2", "sit-pipe-c3"];
  // Stagger three cards across the same journey so lanes never sit empty.
  for (let tick = 0; tick < journey.length + ids.length; tick++) {
    const frame: { id: string; status: Situation["status"] }[] = [];
    ids.forEach((id, i) => {
      const stepIdx = tick - i;
      if (stepIdx >= 0 && stepIdx < journey.length) {
        frame.push({ id, status: journey[stepIdx] });
      }
    });
    SCRIPT_STEPS.push(frame);
  }
}

const MOCK_SEED: Situation[] = [
  {
    id: "sit-pipe-a1",
    signature: "pipe-a1",
    service: "checkout-api",
    title: "Latency spike · checkout-api",
    status: "detected",
    severity: "high",
    memberCount: 58,
    first_seen: Date.now(),
    hypotheses: [],
    suggested_runbook_id: "restart-pod",
    hitl_mode: "auto",
    reversible: true,
    reliability: 0.7,
    suppressed: false,
  },
  {
    id: "sit-pipe-b2",
    signature: "pipe-b2",
    service: "web",
    title: "5xx burst after deploy · web",
    status: "detected",
    severity: "critical",
    memberCount: 132,
    first_seen: Date.now(),
    hypotheses: [],
    suggested_runbook_id: "rollback-deploy",
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.62,
    suppressed: false,
  },
  {
    id: "sit-pipe-c3",
    signature: "pipe-c3",
    service: "payments",
    title: "Memory pressure · payments-worker",
    status: "detected",
    severity: "medium",
    memberCount: 21,
    first_seen: Date.now(),
    hypotheses: [],
    suggested_runbook_id: "scale-service",
    hitl_mode: "hitl",
    reversible: true,
    reliability: 0.55,
    suppressed: false,
  },
];

function useMockJourney(): Situation[] {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (LIVE) return;
    const id = window.setInterval(() => {
      setTick((t) => (t + 1) % SCRIPT_STEPS.length);
    }, 2600);
    return () => window.clearInterval(id);
  }, []);

  return useMemo(() => {
    if (LIVE) return [];
    const frame = SCRIPT_STEPS[tick];
    const statusById = new Map(frame.map((f) => [f.id, f.status]));
    return MOCK_SEED.filter((s) => statusById.has(s.id)).map((s) => ({
      ...s,
      status: statusById.get(s.id)!,
    }));
  }, [tick]);
}

export function Pipeline() {
  const { data: serverSituations } = useLiveData(loadSituations, [] as Situation[]);
  const mockJourney = useMockJourney();
  const [overrides, setOverrides] = useState<Record<string, Partial<Situation>>>({});

  const situations = LIVE ? serverSituations : mockJourney;
  const list = useMemo<Situation[]>(
    () => situations.map((s) => ({ ...s, ...overrides[s.id] })),
    [situations, overrides],
  );
  const live = list.filter((s) => !s.suppressed);

  function update(id: string, patch: Partial<Situation>) {
    setOverrides((o) => ({ ...o, [id]: { ...o[id], ...patch } }));
  }

  async function approve(s: Situation) {
    update(s.id, { status: "acting" });
    try {
      await decideApproval(`appr-${s.id}`, "approved");
      if (!LIVE) setTimeout(() => update(s.id, { status: "resolved" }), 1400);
    } catch {
      update(s.id, { status: "diagnosed" });
    }
  }

  async function reject(s: Situation) {
    update(s.id, { status: "failed" });
    try {
      await decideApproval(`appr-${s.id}`, "rejected");
    } catch {
      /* leave optimistic failed state — Incidents view has the same behavior */
    }
  }

  return (
    <div className="space-y-5">
      <Section>
        <h1 className="text-3xl font-semibold tracking-tightest sm:text-4xl">Live pipeline</h1>
        <p className="mt-2 max-w-[60ch] text-base text-ink-2">
          Every incident, moving through the closed loop in real time — detected, diagnosed, gated for a
          human, remediated, resolved.
        </p>
      </Section>

      {/* Lane headers */}
      <div className="grid grid-cols-5 gap-3">
        {LANES.map((lane) => (
          <div
            key={lane}
            className="rounded-2xl border border-black/[0.06] bg-black/[0.015] px-3 py-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3"
          >
            {LANE_LABEL[lane]}
          </div>
        ))}
      </div>

      {/* ONE flat list; each card placed by gridColumnStart = its lane index. layout FLIPs it across columns. */}
      <LayoutGroup>
        <div className="grid grid-cols-5 items-start gap-3">
          <AnimatePresence initial={false}>
            {live.map((s) => {
              const lane = laneOf(s);
              const col = LANES.indexOf(lane) + 1;
              return (
                <motion.div
                  key={s.id}
                  layout={prefersReduced ? false : "position"}
                  transition={{ layout: springSoft }}
                  initial={{ opacity: 0, scale: 0.96 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.2 } }}
                  style={{ gridColumnStart: col }}
                  className="rounded-4xl border border-black/[0.08] bg-black/[0.02] p-1.5"
                >
                  <div className="rounded-[calc(2rem-6px)] bg-ground-sunken p-3">
                    <div className="flex items-center gap-2">
                      <SevChip sev={s.severity} />
                      <StatusChip status={s.status} />
                      <span className="ml-auto font-mono text-2xs text-ink-3">{timeAgo(s.first_seen)}</span>
                    </div>
                    <div className="mt-2 text-sm font-medium text-ink">{s.title}</div>
                    <div className="mt-1 font-mono text-2xs text-signal-dim">{s.id}</div>
                    {lane === "gate" && s.suggested_runbook_id && (
                      <div className="mt-3 flex gap-2">
                        <CTA icon={false} onClick={() => approve(s)}>
                          Approve
                        </CTA>
                        <CTA icon={false} variant="ghost" onClick={() => reject(s)}>
                          Reject
                        </CTA>
                      </div>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>
      </LayoutGroup>

      {live.length === 0 && (
        <div className="rounded-4xl border border-black/[0.06] p-10 text-center text-ink-3">
          Waiting for incidents — break the demo workload to watch the loop move.
        </div>
      )}
    </div>
  );
}
