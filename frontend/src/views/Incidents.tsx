import { useEffect, useMemo, useState } from "react";
import { AnimatePresence } from "framer-motion";
import {
  ArrowsClockwise,
  Check,
  CircleNotch,
  Cpu,
  FlowArrow,
  Lightning,
  MagicWand,
  ShieldCheck,
  Sparkle,
  X,
} from "@phosphor-icons/react";
import { Bezel, Eyebrow, SevChip, StatusChip, timeAgo, motion as m } from "../components/primitives";
import { loadSituations, decideApproval } from "../data/source";
import { useLiveData } from "../hooks/useLiveData";
import { pushToast } from "../hooks/useToast";
import type { Situation, SituationStatus } from "../data/types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

const stageDefs = [
  { key: "detected", label: "ingestion → correlation", icon: <FlowArrow size={15} weight="light" />, note: "214 alerts → 1 Situation" },
  { key: "diagnosed", label: "rca", icon: <MagicWand size={15} weight="light" />, note: "ranked root cause" },
  { key: "acting", label: "action → governance", icon: <ShieldCheck size={15} weight="light" />, note: "approval gate" },
  { key: "resolved", label: "execute · verify", icon: <Lightning size={15} weight="light" />, note: "reversible remediation" },
];

const order: SituationStatus[] = ["detected", "diagnosed", "acting", "resolved"];

export function Incidents() {
  const { data: seed } = useLiveData(loadSituations, [] as Situation[]);
  const [overrides, setOverrides] = useState<Record<string, Partial<Situation>>>({});
  const [selId, setSelId] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  // merge server data with local optimistic overrides, but let server truth win:
  // once the server shows a terminal status, the optimistic override is stale.
  const list = useMemo<Situation[]>(
    () =>
      seed.map((s) => {
        const o = overrides[s.id];
        if (!o) return s;
        // server reached a terminal state → discard the optimistic flip
        if (s.status === "resolved" || s.status === "failed") return s;
        return { ...s, ...o };
      }),
    [seed, overrides],
  );

  // Prune overrides the server has caught up to, so the map can't pin a stale
  // 'acting' forever (the bug: overrides never cleared → gate reappears).
  useEffect(() => {
    setOverrides((o) => {
      const next: Record<string, Partial<Situation>> = {};
      let changed = false;
      for (const [id, patch] of Object.entries(o)) {
        const srv = seed.find((s) => s.id === id);
        if (srv && (srv.status === "resolved" || srv.status === "failed")) {
          changed = true; // drop it — server is terminal
        } else {
          next[id] = patch;
        }
      }
      return changed ? next : o;
    });
  }, [seed]);

  // keep a valid selection as data streams in
  useEffect(() => {
    if ((selId === null || !list.some((s) => s.id === selId)) && list.length > 0) {
      setSelId(list[0].id);
    }
  }, [list, selId]);

  const sel = useMemo(() => list.find((s) => s.id === selId) ?? null, [list, selId]);

  function update(id: string, patch: Partial<Situation>) {
    setOverrides((o) => ({ ...o, [id]: { ...o[id], ...patch } }));
  }

  async function approve() {
    if (working || !sel) return;
    setWorking(true);
    update(sel.id, { status: "acting" }); // transient: "awaiting outcome"
    try {
      await decideApproval(`appr-${sel.id}`, "approved");
      pushToast("success", `Approved — remediating ${sel.suggested_runbook_id ?? "playbook"}`);
      if (!LIVE) {
        // mock mode: server never advances, so simulate the terminal outcome locally
        setTimeout(
          () =>
            update(sel.id, {
              status: "resolved",
              outcome: { result: "success", health_after: "healthy", mode: "dry_run", steps: [] },
            }),
          1400,
        );
      }
      // live mode: the 5s poll converges to the real server status; Step 1 prunes the override
    } catch (e) {
      pushToast("error", `Approval failed: ${e instanceof Error ? e.message : "unknown"}`);
      update(sel.id, { status: "diagnosed" }); // roll the optimistic flip back
    } finally {
      setWorking(false);
    }
  }

  async function reject() {
    if (working || !sel) return;
    setWorking(true);
    update(sel.id, { status: "failed" });
    try {
      await decideApproval(`appr-${sel.id}`, "rejected");
      pushToast("success", "Rejected — no action taken");
      if (!LIVE) {
        update(sel.id, {
          status: "failed",
          outcome: { result: "failure", health_after: "aborted:rejected", mode: "dry_run", steps: [] },
        });
      }
    } catch (e) {
      pushToast("error", `Reject failed: ${e instanceof Error ? e.message : "unknown"}`);
      update(sel.id, { status: "diagnosed" });
    } finally {
      setWorking(false);
    }
  }

  const stageIndex = sel ? order.indexOf(sel.status === "failed" ? "acting" : sel.status) : 0;

  return (
    <div className="space-y-5">
      <div>
        <Eyebrow>
          <span className="h-1.5 w-1.5 animate-beat rounded-full bg-sev-warn" /> Incident workspace · on-call
        </Eyebrow>
        <h1 className="mt-4 text-4xl font-semibold tracking-tightest sm:text-5xl">Situations, not alerts.</h1>
        <p className="mt-3 max-w-[56ch] text-base leading-relaxed text-ink-2">
          Each row is an entire alert storm collapsed to one working incident. Open one to walk the pipeline
          and clear the approval gate.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* queue */}
        <div className="lg:col-span-5">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Open situations</span>
            <span className="font-mono text-2xs text-ink-3">{list.filter((s) => !["resolved", "suppressed"].includes(s.status)).length} active</span>
          </div>
          <div className="space-y-3">
            {list.map((s) => {
              const active = s.id === selId;
              return (
                <button key={s.id} onClick={() => setSelId(s.id)} className="block w-full text-left">
                  <div
                    className={`rounded-4xl p-1.5 transition-all duration-500 ease-fluid ${
                      active ? "border border-signal/40 bg-signal/[0.06] shadow-glow" : "border border-black/[0.06] bg-black/[0.02] hover:bg-black/[0.04]"
                    }`}
                  >
                    <div className="rounded-[calc(2rem-6px)] bg-ground-sunken p-4">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <SevChip sev={s.severity} />
                          <StatusChip status={s.status} />
                        </div>
                        <span className="font-mono text-2xs text-ink-3">{timeAgo(s.first_seen)}</span>
                      </div>
                      <div className="mt-2.5 text-sm font-medium tracking-tight text-ink">{s.title}</div>
                      <div className="mt-1 flex items-center gap-3 font-mono text-2xs text-ink-3">
                        <span className="text-signal-dim">{s.id}</span>
                        <span>·</span>
                        <span>{s.memberCount} alerts</span>
                        <span className="ml-auto flex items-center gap-1">
                          <span className={`h-1 w-1 rounded-full ${s.reliability >= 0.8 ? "bg-signal" : "bg-ink-4"}`} />
                          rel {s.reliability.toFixed(2)}
                        </span>
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* detail */}
        {sel ? (
        <div className="lg:col-span-7">
          <AnimatePresence mode="wait">
            <m.div key={sel.id} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}>
              <Bezel coreClassName="p-6">
                {/* header */}
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <SevChip sev={sel.severity} />
                      <StatusChip status={sel.status} />
                    </div>
                    <h2 className="mt-3 text-2xl font-semibold tracking-tight">{sel.title}</h2>
                    <div className="mt-1.5 flex items-center gap-3 font-mono text-2xs text-ink-3">
                      <span className="text-signal-dim">{sel.id}</span>
                      <span>signature {sel.signature}</span>
                      <span>· {sel.memberCount} alerts collapsed</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1.5 font-mono text-2xs text-ink-2">
                    <Cpu size={14} weight="light" /> {sel.service}
                  </div>
                </div>

                {/* pipeline rail */}
                <div className="mt-6 rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
                  <div className="space-y-1.5">
                    {stageDefs.map((st, i) => {
                      const done = i < stageIndex;
                      const now = i === stageIndex && sel.status !== "resolved" && sel.status !== "failed";
                      const doneAll = sel.status === "resolved";
                      const isDone = done || doneAll;
                      return (
                        <div key={st.key} className={`flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors duration-500 ${now ? "bg-signal/[0.07]" : ""}`}>
                          <span className={`flex h-7 w-7 flex-none items-center justify-center rounded-lg ${isDone ? "bg-sev-ok/15 text-sev-ok" : now ? "bg-signal/15 text-signal" : "bg-black/[0.05] text-ink-3"}`}>
                            {isDone ? <Check size={14} weight="bold" /> : now && working ? <CircleNotch size={14} weight="bold" className="animate-spin" /> : st.icon}
                          </span>
                          <div className="min-w-0">
                            <div className={`text-sm ${isDone || now ? "text-ink" : "text-ink-3"}`}>{st.label}</div>
                            <div className="font-mono text-2xs text-ink-3">{st.note}</div>
                          </div>
                          {now && <span className="ml-auto font-mono text-2xs text-signal-dim">in progress</span>}
                          {isDone && <span className="ml-auto font-mono text-2xs text-sev-ok">done</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* hypotheses */}
                <div className="mt-5">
                  <div className="mb-2 flex items-center gap-2">
                    <Sparkle size={14} weight="light" className="text-signal" />
                    <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Ranked root cause</span>
                  </div>
                  <div className="space-y-2">
                    {sel.hypotheses.map((h, i) => (
                      <div key={i} className={`rounded-xl border p-3 ${i === 0 ? "border-signal/25 bg-signal/[0.05]" : "border-black/[0.06] bg-black/[0.02]"}`}>
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-sm text-ink-2">{h.description}</span>
                          <span className="flex-none font-mono text-2xs text-ink-3">conf {h.confidence.toFixed(2)}</span>
                        </div>
                        <div className="mt-2 flex items-center gap-2">
                          <div className="h-1 flex-1 overflow-hidden rounded-full bg-black/[0.08]">
                            <div className={`h-full rounded-full ${i === 0 ? "bg-signal" : "bg-ink-4"}`} style={{ width: `${h.confidence * 100}%` }} />
                          </div>
                          {h.suggested_runbook_id && <span className="rounded-md bg-black/[0.05] px-2 py-0.5 font-mono text-2xs text-ink-2">{h.suggested_runbook_id}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* the gate / result */}
                <div className="mt-5 rounded-2xl border border-black/[0.06] bg-black/[0.03] p-4">
                  {sel.status === "resolved" ? (
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-sev-ok/15 text-sev-ok"><Check size={17} weight="bold" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">Resolved · <span className="font-mono text-sev-ok">healthy</span></div>
                        <div className="font-mono text-2xs text-ink-3">outcome labeled → reliability rising → next matching storm may be suppressed</div>
                      </div>
                    </div>
                  ) : sel.status === "failed" ? (
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-sev-warn/15 text-sev-warn"><X size={17} weight="bold" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">No action taken · <span className="font-mono text-sev-warn">aborted:rejected</span></div>
                        <div className="font-mono text-2xs text-ink-3">gate failed closed — nothing executed</div>
                      </div>
                    </div>
                  ) : sel.hitl_mode === "auto" ? (
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-signal/15 text-signal"><Lightning size={17} weight="light" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">Auto-remediating · <span className="font-mono text-signal">{sel.suggested_runbook_id}</span></div>
                        <div className="font-mono text-2xs text-ink-3">graduated playbook — RBAC-checked, running without a human</div>
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="flex h-2 w-2 animate-beat rounded-full bg-sev-warn" />
                        <span className="text-sm font-medium text-ink">Human approval required</span>
                        <span className="ml-auto rounded-md bg-black/[0.05] px-2 py-0.5 font-mono text-2xs text-ink-2">{sel.suggested_runbook_id} · hitl</span>
                      </div>
                      <p className="mt-1.5 font-mono text-2xs text-ink-3">action-service is authorized to <span className="text-ink-2">execute</span> this reversible playbook. Approve to run it, or reject to hold.</p>
                      <div className="mt-3 flex gap-2">
                        <button onClick={approve} disabled={working} className="group flex items-center gap-2 rounded-full bg-signal px-5 py-2.5 text-sm font-medium text-white transition-all duration-300 ease-fluid active:scale-[0.97] disabled:opacity-50">
                          {working ? <CircleNotch size={15} weight="bold" className="animate-spin" /> : <Check size={15} weight="bold" />}
                          {working ? "Executing…" : "Approve & remediate"}
                        </button>
                        <button onClick={reject} disabled={working} className="flex items-center gap-2 rounded-full border border-black/[0.10] bg-black/[0.04] px-5 py-2.5 text-sm text-ink-2 transition-all duration-300 ease-fluid hover:bg-black/[0.06] active:scale-[0.97]">
                          <X size={15} weight="bold" /> Reject
                        </button>
                        {!LIVE && (
                          <button onClick={() => update(sel.id, { status: "detected", outcome: undefined })} className="ml-auto flex items-center gap-1.5 rounded-full px-3 py-2.5 font-mono text-2xs text-ink-3 hover:text-ink-2">
                            <ArrowsClockwise size={13} weight="light" /> reset
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </Bezel>
            </m.div>
          </AnimatePresence>
        </div>
        ) : (
          <div className="lg:col-span-7 flex items-center justify-center rounded-4xl border border-black/[0.06] p-12 text-ink-3">
            Waiting for situations…
          </div>
        )}
      </div>
    </div>
  );
}
