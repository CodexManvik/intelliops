import { useEffect, useRef, useState } from "react";
import { AnimatePresence } from "framer-motion";
import {
  ArrowSquareOut,
  CaretRight,
  CheckCircle,
  CircleNotch,
  Flag,
  Lightbulb,
  ListChecks,
  Wrench,
  XCircle,
} from "@phosphor-icons/react";
import { Bezel, Eyebrow, timeAgo, motion as m } from "../components/primitives";
import { loadAgentRun, loadAgentRuns, openAgentRunStream } from "../data/source";
import { pushToast } from "../hooks/useToast";
import type { RunSummary, TraceStep } from "../data/types";

const RUN_LIST_POLL_MS = 4000;

const statusSkin: Record<string, string> = {
  running: "text-signal bg-signal/10",
  succeeded: "text-sev-ok bg-sev-ok/10",
  gave_up: "text-sev-warn bg-sev-warn/10",
  failed: "text-sev-crit bg-sev-crit/10",
};
const statusLabel: Record<string, string> = {
  running: "Running",
  succeeded: "Succeeded",
  gave_up: "Gave up",
  failed: "Failed",
};

function RunStatusChip({ status }: { status: string }) {
  const skin = statusSkin[status] ?? "text-ink-3 bg-black/[0.05]";
  const label = statusLabel[status] ?? status;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-2xs ${skin}`}>
      {status === "running" && <CircleNotch size={10} weight="bold" className="animate-spin" />}
      {label}
    </span>
  );
}

function isTerminal(status: string): boolean {
  return status === "succeeded" || status === "failed" || status === "gave_up";
}

/* ---------------------------------------------------------------------------
   Expandable trace rows — one per TraceStep kind, collapsed one-liner ↔
   expanded detail. This is the core ask: a CI-log-style dropdown per step.
--------------------------------------------------------------------------- */

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-black/[0.04] p-3 font-mono text-2xs leading-relaxed text-ink-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function TraceRow({ step }: { step: TraceStep }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((o) => !o);

  let icon: JSX.Element;
  let summary: React.ReactNode;
  let expandable = true;
  let tone = "text-ink-2";

  if (step.kind === "tool_call") {
    icon = <Wrench size={14} weight="light" />;
    summary = (
      <>
        <span className="text-ink">{step.tool ?? "tool"}</span>
        {step.result_summary && <span className="text-ink-3"> — {step.result_summary}</span>}
      </>
    );
  } else if (step.kind === "model_turn") {
    icon = <Lightbulb size={14} weight="light" />;
    summary = <span className="text-ink-2">Reasoning</span>;
    tone = "text-signal-dim";
  } else if (step.kind === "submit") {
    icon = <ListChecks size={14} weight="light" />;
    const name = (step.detail?.name as string | undefined) ?? "runbook";
    summary = (
      <>
        <span className="text-ink-2">Drafted </span>
        <span className="text-ink">{name}</span>
      </>
    );
  } else {
    // outcome — terminal row, not a dropdown; renders inline status instead.
    icon = <Flag size={14} weight="light" />;
    expandable = false;
    const status = (step.detail?.status as string | undefined) ?? "unknown";
    const proposalId = step.detail?.proposal_id as string | null | undefined;
    const succeeded = status === "succeeded";
    summary = (
      <div className="flex flex-1 flex-wrap items-center gap-2">
        <span className={succeeded ? "text-sev-ok" : "text-sev-warn"}>
          {succeeded ? <CheckCircle size={14} weight="fill" className="inline -mt-0.5 mr-1" /> : <XCircle size={14} weight="fill" className="inline -mt-0.5 mr-1" />}
          {succeeded ? "Draft succeeded" : `Agent ${status.replace("_", " ")}`}
        </span>
        {succeeded && proposalId && (
          <span className="rounded-md bg-black/[0.05] px-2 py-0.5 font-mono text-2xs text-ink-3">
            proposal {proposalId} · review in Governance
          </span>
        )}
        {!succeeded && (
          <span className="font-mono text-2xs text-ink-3">reason: {status}</span>
        )}
      </div>
    );
  }

  return (
    <div className={`rounded-xl border border-black/[0.06] bg-black/[0.02] transition-colors ${expandable ? "hover:bg-black/[0.035]" : ""}`}>
      <button
        onClick={expandable ? toggle : undefined}
        disabled={!expandable}
        className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm ${expandable ? "cursor-pointer" : "cursor-default"}`}
      >
        <span className={`flex h-6 w-6 flex-none items-center justify-center rounded-lg bg-black/[0.05] ${tone}`}>{icon}</span>
        <span className="min-w-0 flex-1 truncate">{summary}</span>
        {expandable && (
          <CaretRight size={12} weight="bold" className={`flex-none text-ink-3 transition-transform duration-300 ${open ? "rotate-90" : ""}`} />
        )}
      </button>

      <AnimatePresence initial={false}>
        {expandable && open && (
          <m.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.32, 0.72, 0, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-black/[0.06] px-3 pb-3 pt-2.5">
              {step.kind === "tool_call" && (
                <>
                  {step.arguments && Object.keys(step.arguments).length > 0 && (
                    <div>
                      <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Arguments</span>
                      <JsonBlock value={step.arguments} />
                    </div>
                  )}
                  {step.result_summary && (
                    <div className="mt-2.5">
                      <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Result</span>
                      <p className="mt-1.5 text-2xs leading-relaxed text-ink-2">{step.result_summary}</p>
                    </div>
                  )}
                </>
              )}

              {step.kind === "model_turn" && (
                <p className="whitespace-pre-wrap text-2xs leading-relaxed text-ink-2">{step.text ?? "—"}</p>
              )}

              {step.kind === "submit" && (
                <>
                  {Array.isArray(step.detail?.actions) && (step.detail!.actions as unknown[]).length > 0 && (
                    <div>
                      <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Actions</span>
                      <ul className="mt-1.5 space-y-0.5">
                        {(step.detail!.actions as unknown[]).map((a, i) => (
                          <li key={i} className="font-mono text-2xs text-ink-2">
                            • {typeof a === "string" ? a : JSON.stringify(a)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {typeof step.detail?.rationale === "string" && (
                    <div className="mt-2.5">
                      <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Rationale</span>
                      <p className="mt-1.5 text-2xs leading-relaxed text-ink-2">{step.detail.rationale as string}</p>
                    </div>
                  )}
                  {Array.isArray(step.detail?.cited_facts) && (step.detail!.cited_facts as unknown[]).length > 0 && (
                    <div className="mt-2.5">
                      <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Cited facts</span>
                      <ul className="mt-1.5 space-y-0.5">
                        {(step.detail!.cited_facts as unknown[]).map((f, i) => (
                          <li key={i} className="font-mono text-2xs text-ink-3">• {typeof f === "string" ? f : JSON.stringify(f)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   AgentActivity — left: run feed; right: the selected run's live/stored trace
--------------------------------------------------------------------------- */

export function AgentActivity({
  focusRun,
  setFocusRun,
}: {
  focusRun?: string | null;
  setFocusRun?: (id: string | null) => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [loadingSteps, setLoadingSteps] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // Feed: load once, then poll — same shape as the rest of the console's
  // "live-ish" views (Incidents/Governance poll via useLiveData; this view
  // manages its own interval since it also needs to drive stream lifecycle).
  useEffect(() => {
    let alive = true;
    const tick = () =>
      loadAgentRuns()
        .then((r) => alive && setRuns(r))
        .catch(() => {
          /* transient fetch failure — next poll tick recovers */
        });
    tick();
    const id = window.setInterval(tick, RUN_LIST_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  // Auto-select: prefer an externally-focused run (deep-link from Incidents'
  // Draft button); otherwise fall back to the most recent run once the feed
  // has loaded and nothing is selected yet.
  useEffect(() => {
    if (focusRun) {
      setSelectedId(focusRun);
      setFocusRun?.(null); // consume — further list refreshes shouldn't re-force selection
      return;
    }
    if (selectedId === null && runs.length > 0) {
      setSelectedId(runs[0].run_id);
    }
  }, [focusRun, runs, selectedId, setFocusRun]);

  const selectedRun = runs.find((r) => r.run_id === selectedId) ?? null;

  // Trace: stream live steps for an in-flight run, or load the stored trace
  // for a terminal one. Cleans up the EventSource on unmount / run change /
  // once an outcome step arrives — no leaked connections.
  useEffect(() => {
    esRef.current?.close();
    esRef.current = null;
    setSteps([]);

    if (!selectedId) return;

    const running = !selectedRun || !isTerminal(selectedRun.status);

    if (!running) {
      setLoadingSteps(true);
      let alive = true;
      loadAgentRun(selectedId)
        .then((s) => alive && setSteps(s))
        .catch((e) => alive && pushToast("error", `Failed to load trace: ${e instanceof Error ? e.message : "unknown"}`))
        .finally(() => alive && setLoadingSteps(false));
      return () => {
        alive = false;
      };
    }

    // running (or status not yet known, e.g. just-created) → stream live
    setLoadingSteps(true);
    let closed = false;
    const es = openAgentRunStream(selectedId);
    esRef.current = es;

    es.onmessage = (event) => {
      setLoadingSteps(false);
      try {
        const step = JSON.parse(event.data) as TraceStep;
        setSteps((cur) => {
          if (cur.some((s) => s.seq === step.seq)) return cur; // seq de-dup (replay + live overlap)
          return [...cur, step].sort((a, b) => a.seq - b.seq);
        });
        if (step.kind === "outcome" && !closed) {
          closed = true;
          es.close();
          // nudge the feed so the run's status/step_count catches up promptly
          loadAgentRuns().then(setRuns).catch(() => {});
        }
      } catch {
        // malformed SSE payload — ignore this event, keep the connection open
      }
    };
    es.onerror = () => {
      setLoadingSteps(false);
      // EventSource auto-reconnects on transient errors; if the run has
      // actually finished server-side, the next feed poll flips selectedRun
      // to terminal and this effect re-runs onto the loadAgentRun branch.
    };

    return () => {
      closed = true;
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedRun?.status intentionally drives running vs. terminal branch selection, not identity
  }, [selectedId, selectedRun?.status]);

  return (
    <div className="space-y-5">
      <div>
        <Eyebrow>
          <ListChecks size={12} weight="light" /> Agent Activity · AI runbook author
        </Eyebrow>
        <h1 className="mt-4 text-4xl font-semibold tracking-tightest sm:text-5xl">
          Watch the agent <span className="text-signal">think.</span>
        </h1>
        <p className="mt-3 max-w-[58ch] text-base leading-relaxed text-ink-2">
          Every draft run — the reasoning, each tool call and its result, and the final draft — recorded
          step by step. Live while the agent works, replayable after.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* run feed */}
        <div className="lg:col-span-4">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Recent runs</span>
            <span className="font-mono text-2xs text-ink-3">{runs.length}</span>
          </div>
          <div className="space-y-2.5">
            {runs.map((r) => {
              const active = r.run_id === selectedId;
              return (
                <button key={r.run_id} onClick={() => setSelectedId(r.run_id)} className="block w-full text-left">
                  <div
                    className={`rounded-3xl p-1.5 transition-all duration-500 ease-fluid ${
                      active
                        ? "border border-signal/40 bg-signal/[0.06] shadow-glow"
                        : "border border-black/[0.06] bg-black/[0.02] hover:bg-black/[0.04]"
                    }`}
                  >
                    <div className="rounded-[calc(1.5rem-6px)] bg-ground-sunken p-3.5">
                      <div className="flex items-center justify-between gap-2">
                        <RunStatusChip status={r.status} />
                        <span className="font-mono text-2xs text-ink-3">{timeAgo(r.started_at)}</span>
                      </div>
                      <div className="mt-2 font-mono text-2xs text-signal-dim">{r.signature}</div>
                      <div className="mt-1 flex items-center gap-2 font-mono text-2xs text-ink-3">
                        <span>{r.run_id}</span>
                        <span>·</span>
                        <span>{r.step_count} steps</span>
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
            {runs.length === 0 && (
              <div className="rounded-2xl border border-black/[0.06] p-6 text-center text-2xs text-ink-3">
                No runs yet. Trigger "Draft a runbook with AI" from an incident to start one.
              </div>
            )}
          </div>
        </div>

        {/* selected run's trace */}
        <div className="lg:col-span-8">
          {selectedRun ? (
            <AnimatePresence mode="wait">
              <m.div
                key={selectedRun.run_id}
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
              >
                <Bezel coreClassName="p-6">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <RunStatusChip status={selectedRun.status} />
                        <span className="font-mono text-2xs text-ink-3">{selectedRun.run_id}</span>
                      </div>
                      <h2 className="mt-3 text-xl font-semibold tracking-tight">
                        Drafting for signature <span className="text-signal-dim">{selectedRun.signature}</span>
                      </h2>
                      <div className="mt-1.5 font-mono text-2xs text-ink-3">
                        started {timeAgo(selectedRun.started_at)} · {selectedRun.step_count} steps recorded
                      </div>
                    </div>
                    {selectedRun.status === "succeeded" && selectedRun.proposal_id && (
                      <span className="flex items-center gap-1.5 rounded-full border border-black/[0.08] bg-black/[0.03] px-3 py-1.5 font-mono text-2xs text-ink-2">
                        <ArrowSquareOut size={13} weight="light" /> {selectedRun.proposal_id}
                      </span>
                    )}
                  </div>

                  <div className="mt-5 space-y-2">
                    {steps.map((s) => (
                      <TraceRow key={s.seq} step={s} />
                    ))}
                    {loadingSteps && steps.length === 0 && (
                      <div className="flex items-center gap-2 rounded-xl border border-black/[0.06] bg-black/[0.02] px-3 py-4 font-mono text-2xs text-ink-3">
                        <CircleNotch size={13} weight="bold" className="animate-spin" /> waiting for the agent…
                      </div>
                    )}
                    {!loadingSteps && steps.length === 0 && (
                      <div className="rounded-xl border border-black/[0.06] p-6 text-center font-mono text-2xs text-ink-3">
                        No trace recorded for this run.
                      </div>
                    )}
                  </div>
                </Bezel>
              </m.div>
            </AnimatePresence>
          ) : (
            <div className="flex items-center justify-center rounded-4xl border border-black/[0.06] p-12 text-ink-3">
              Select a run to see its trace.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
