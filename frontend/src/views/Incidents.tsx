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
import { loadSituations, loadSituationDetail, decideApproval, loadMetrics, loadOutcomes } from "../data/source";
import { useLiveData } from "../hooks/useLiveData";
import { pushToast } from "../hooks/useToast";
import type { Situation, SituationStatus, Metrics, OutcomeRow } from "../data/types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

const stageDefs = [
  { key: "detected", label: "ingestion → correlation", icon: <FlowArrow size={15} weight="light" />, note: "alerts → 1 Situation" },
  { key: "diagnosed", label: "rca", icon: <MagicWand size={15} weight="light" />, note: "ranked root cause" },
  { key: "acting", label: "action → governance", icon: <ShieldCheck size={15} weight="light" />, note: "approval gate" },
  { key: "resolved", label: "execute · verify", icon: <Lightning size={15} weight="light" />, note: "reversible remediation" },
];

const order: SituationStatus[] = ["detected", "diagnosed", "acting", "resolved"];

const METRIC_DOCS: Record<string, { title: string; formula: string; meaning: string }> = {
  noise: {
    title: "Noise reduction",
    meaning: "How much raw alert noise IntelliOps collapsed into a handful of real incidents.",
    formula: "1 − (situations ÷ raw alerts ingested)",
  },
  mttr: {
    title: "MTTR",
    meaning: "Mean Time To Resolve — average time from an incident first appearing to it being fixed.",
    formula: "avg(resolved_at − first_seen) over successful remediations",
  },
  auto: {
    title: "Auto-remediated",
    meaning: "Share of fixes that ran automatically, because the playbook had earned autonomy (≥3 clean successes).",
    formula: "auto-mode outcomes ÷ all outcomes",
  },
  success: {
    title: "Success rate",
    meaning: "Share of remediations that verified healthy afterward.",
    formula: "successful outcomes ÷ all outcomes",
  },
};

function MetricCard({
  docKey, value, sub,
}: { docKey: keyof typeof METRIC_DOCS; value: string; sub: string }) {
  const [open, setOpen] = useState(false);
  const d = METRIC_DOCS[docKey];
  return (
    <button onClick={() => setOpen((o) => !o)} className="block w-full text-left">
      <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4 transition-colors hover:bg-black/[0.04]">
        <div className="flex items-center justify-between">
          <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">{d.title}</span>
          <span className="font-mono text-2xs text-ink-4">{open ? "−" : "?"}</span>
        </div>
        <div className="mt-1 text-2xl font-semibold tracking-tightest tnum">{value}</div>
        <div className="font-mono text-2xs text-ink-3">{sub}</div>
        {open && (
          <div className="mt-3 border-t border-black/[0.06] pt-3">
            <p className="text-2xs leading-relaxed text-ink-2">{d.meaning}</p>
            <p className="mt-1.5 font-mono text-2xs text-ink-3">= {d.formula}</p>
          </div>
        )}
      </div>
    </button>
  );
}

export function Incidents() {
  const { data: seed } = useLiveData(loadSituations, [] as Situation[]);
  const { data: metrics } = useLiveData(loadMetrics, {
    alertsIngested: 0, situationsOpen: 0, noiseReductionPct: 0, mttrMinutes: 0,
    autoRemediatedPct: 0, suppressedToday: 0, approvalsPending: 0, successRate: 0,
  } as Metrics);
  const { data: recentOutcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
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

  // fetch the full detail (member_events, baseline, evidence, explanation) for the
  // selected situation — the list endpoint returns these too, but the detail fetch
  // keeps the drill-down panel authoritative.
  const { data: detail } = useLiveData(
    useMemo(() => () => (selId ? loadSituationDetail(selId) : Promise.resolve(null as Situation | null)), [selId]),
    null as Situation | null,
  );
  const shown = sel && detail && detail.id === selId ? { ...sel, ...detail, ...overrides[selId] } : sel;

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
              outcome: {
                result: "success",
                health_after: "healthy",
                mode: "dry_run",
                steps: [],
                preflight: { passed: true, detail: "sandbox: clone healthy in 8s", mode: "k8s" },
              },
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

  const stageIndex = shown ? order.indexOf(shown.status === "failed" ? "acting" : shown.status) : 0;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <MetricCard docKey="noise" value={`${metrics.noiseReductionPct}%`} sub={`${metrics.alertsIngested.toLocaleString()} alerts → ${metrics.situationsOpen} open`} />
        <MetricCard docKey="mttr" value={metrics.mttrMinutes > 0 ? `${metrics.mttrMinutes}m` : "—"} sub={metrics.mttrMinutes > 0 ? "mean time to resolve" : "no fixes yet"} />
        <MetricCard docKey="auto" value={`${metrics.autoRemediatedPct}%`} sub="ran without a human" />
        <MetricCard docKey="success" value={`${Math.round(metrics.successRate * 100)}%`} sub="verified healthy" />
      </div>

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
        {sel && shown ? (
        <div className="lg:col-span-7">
          <AnimatePresence mode="wait">
            <m.div key={sel.id} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}>
              <Bezel coreClassName="p-6">
                {/* header */}
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <SevChip sev={shown.severity} />
                      <StatusChip status={shown.status} />
                    </div>
                    <h2 className="mt-3 text-2xl font-semibold tracking-tight">{shown.title}</h2>
                    <div className="mt-1.5 flex items-center gap-3 font-mono text-2xs text-ink-3">
                      <span className="text-signal-dim">{shown.id}</span>
                      <span>signature {shown.signature}</span>
                      <span>· {shown.memberCount} alerts collapsed</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1.5 font-mono text-2xs text-ink-2">
                    <Cpu size={14} weight="light" /> {shown.service}
                  </div>
                </div>

                {/* pipeline rail */}
                <div className="mt-6 rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
                  <div className="space-y-1.5">
                    {stageDefs.map((st, i) => {
                      const done = i < stageIndex;
                      const now = i === stageIndex && shown.status !== "resolved" && shown.status !== "failed";
                      const doneAll = shown.status === "resolved";
                      const isDone = done || doneAll;
                      return (
                        <div key={st.key} className={`flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors duration-500 ${now ? "bg-signal/[0.07]" : ""}`}>
                          <span className={`flex h-7 w-7 flex-none items-center justify-center rounded-lg ${isDone ? "bg-sev-ok/15 text-sev-ok" : now ? "bg-signal/15 text-signal" : "bg-black/[0.05] text-ink-3"}`}>
                            {isDone ? <Check size={14} weight="bold" /> : now && working ? <CircleNotch size={14} weight="bold" className="animate-spin" /> : st.icon}
                          </span>
                          <div className="min-w-0">
                            <div className={`text-sm ${isDone || now ? "text-ink" : "text-ink-3"}`}>{st.label}</div>
                            <div className="font-mono text-2xs text-ink-3">
                              {st.key === "detected" ? `${shown.memberCount ?? "—"} alerts → 1 Situation` : st.note}
                            </div>
                          </div>
                          {now && <span className="ml-auto font-mono text-2xs text-signal-dim">in progress</span>}
                          {isDone && <span className="ml-auto font-mono text-2xs text-sev-ok">done</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* what broke — member events + z-score vs baseline */}
                {shown.member_events && shown.member_events.length > 0 && (
                  <div className="mt-5">
                    <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">What broke — the signal</div>
                    <div className="space-y-1">
                      {shown.member_events.slice(0, 6).map((ev, i) => {
                        const b = shown.baseline?.[ev.name];
                        return (
                          <div key={i} className="flex items-center gap-3 rounded-lg bg-black/[0.02] px-3 py-1.5 font-mono text-2xs">
                            <span className="text-ink">{ev.name}</span>
                            <span className="text-signal-dim">{ev.value ?? "—"}</span>
                            {b && <span className="text-ink-3">vs baseline {b.mean.toFixed(1)}±{b.std.toFixed(1)}</span>}
                            {shown.peak_score != null && i === 0 && <span className="ml-auto text-sev-warn">z ≈ {shown.peak_score.toFixed(1)}</span>}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* hypotheses */}
                <div className="mt-5">
                  <div className="mb-2 flex items-center gap-2">
                    <Sparkle size={14} weight="light" className="text-signal" />
                    <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Ranked root cause</span>
                  </div>
                  <div className="space-y-2">
                    {shown.hypotheses.map((h, i) => (
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
                        {h.evidence && h.evidence.length > 0 && (
                          <ul className="mt-2 space-y-0.5">
                            {h.evidence.map((e, j) => (
                              <li key={j} className="font-mono text-2xs text-ink-3">• {e}</li>
                            ))}
                          </ul>
                        )}
                        {i === 0 && h.explanation && (
                          <div className="mt-2 rounded-lg bg-black/[0.03] p-2 text-2xs leading-relaxed text-ink-2">
                            <span className="font-mono text-ink-3">
                              {h.explanation_source === "llm"
                                ? "AI explanation"
                                : h.explanation_source === "template"
                                  ? "Template explanation (no LLM)"
                                  : "explanation"}
                              : </span>
                            {h.explanation}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* the gate / result */}
                <div className="mt-5 rounded-2xl border border-black/[0.06] bg-black/[0.03] p-4">
                  {shown.status === "resolved" ? (
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-full bg-sev-ok/15 text-sev-ok"><Check size={17} weight="bold" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">
                          Resolved · <span className="font-mono text-sev-ok">{shown.outcome?.health_after ?? "resolved"}</span>
                          {shown.outcome?.mode === "dry_run" && <span className="ml-2 rounded-md bg-black/[0.05] px-1.5 py-0.5 font-mono text-2xs text-ink-3">dry-run</span>}
                        </div>
                        {shown.outcome?.steps && shown.outcome.steps.length > 0 && (
                          <div className="mt-1 font-mono text-2xs text-ink-3">steps: {shown.outcome.steps.join(" → ")}</div>
                        )}
                        {shown.outcome?.preflight && shown.outcome.preflight.mode !== "off" && (
                          <div className="mt-1 font-mono text-2xs text-ink-3">
                            🧪 pre-flight:{" "}
                            {shown.outcome.preflight.passed ? (
                              <span className="text-sev-ok">rehearsed in sandbox — passed</span>
                            ) : (
                              <span className="text-sev-warn">failed — {shown.outcome.preflight.detail}</span>
                            )}
                          </div>
                        )}
                        <div className="font-mono text-2xs text-ink-3">outcome labeled → reliability rising → next matching storm may be suppressed</div>
                      </div>
                    </div>
                  ) : shown.status === "failed" ? (
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-full bg-sev-warn/15 text-sev-warn"><X size={17} weight="bold" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">
                          No action taken · <span className="font-mono text-sev-warn">{shown.outcome?.health_after ?? "aborted"}</span>
                        </div>
                        <div className="font-mono text-2xs text-ink-3">gate failed closed — nothing executed</div>
                      </div>
                    </div>
                  ) : shown.hitl_mode === "auto" ? (
                    <div className="flex items-center gap-3">
                      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-signal/15 text-signal"><Lightning size={17} weight="light" /></span>
                      <div>
                        <div className="text-sm font-medium text-ink">Auto-remediating · <span className="font-mono text-signal">{shown.suggested_runbook_id}</span></div>
                        <div className="font-mono text-2xs text-ink-3">graduated playbook — RBAC-checked, running without a human</div>
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="flex h-2 w-2 animate-beat rounded-full bg-sev-warn" />
                        <span className="text-sm font-medium text-ink">Human approval required</span>
                        <span className="ml-auto rounded-md bg-black/[0.05] px-2 py-0.5 font-mono text-2xs text-ink-2">{shown.suggested_runbook_id} · hitl</span>
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

      {recentOutcomes.length > 0 && (
        <div className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
          <div className="mb-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Recent outcomes</div>
          <div className="space-y-1">
            {recentOutcomes.slice(0, 5).map((o, i) => (
              <div key={i} className="flex items-center gap-3 font-mono text-2xs">
                <span className="text-ink-3">{timeAgo(o.ts)}</span>
                <span className="w-40 truncate text-ink-2">{o.playbook_id}</span>
                <span className="ml-auto text-ink-3">{o.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
