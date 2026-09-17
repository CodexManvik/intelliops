import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise,
  Broadcast,
  CheckCircle,
  Circuitry,
  Cpu,
  Gauge,
  HandPalm,
  Lightning,
  MagicWand,
  Pulse,
  ShieldCheck,
  Sparkle,
  Waveform,
  XCircle,
} from "@phosphor-icons/react";
import {
  Bezel,
  CTA,
  Eyebrow,
  Sparkline,
  SevChip,
  StatusChip,
  timeAgo,
} from "../components/primitives";
import { Reveal as Section } from "../hooks/useReveal";
import { useData } from "../hooks/useData";
import { useLiveData } from "../hooks/useLiveData";
import {
  loadMetrics,
  loadOutcomes,
  loadPlaybooks,
  loadProposals,
  loadSituations,
  loadSystem,
  loadMetricHistory,
} from "../data/source";
import { system as mockSystem } from "../data/mock";
import { LiveChart } from "../components/LiveChart";
import { isGraduated } from "../data/types";
import type {
  MetricHistory,
  Metrics,
  OutcomeRow,
  RemediationResult,
  Playbook,
  ProposedPlaybook,
  Situation,
  SystemInfo,
} from "../data/types";
import type { View } from "../components/Shell";

// Metric families Meridian exposes and Prometheus actually scrapes.
const METRIC_CHOICES = [
  { key: "cpu_usage", label: "cpu", unit: "%" },
  { key: "memory_usage_mb", label: "memory", unit: "MB" },
  { key: "latency_p99_ms", label: "p99", unit: "ms" },
  { key: "meridian_error_rate", label: "errors", unit: "" },
  { key: "queue_depth", label: "queue", unit: "" },
  { key: "service_up", label: "up", unit: "" },
];

const EMPTY_HISTORY: MetricHistory = {
  metric: "cpu_usage",
  available: false,
  start: 0,
  end: 0,
  step_seconds: 0,
  series: [],
};

/* ---------------------------------------------------------------------------
   Count-up — eases a number to its target once, respecting reduced-motion.
   The resting render is always the true value, so a frozen clock never
   strands a KPI at 0 (same discipline as the CSS reveals).
--------------------------------------------------------------------------- */
function useCountUp(target: number, ms = 900): number {
  const [n, setN] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const start = from.current;
    if (reduce || start === target) {
      setN(target);
      from.current = target;
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic — matches the fluid feel
      setN(start + (target - start) * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return n;
}

/* ---------------------------------------------------------------------------
   Hero KPI — a big count-up stat in a Bezel, with a sparkline underline.
--------------------------------------------------------------------------- */
function Kpi({
  label,
  value,
  suffix = "",
  sub,
  spark,
  color = "rgb(var(--chart-1))",
  decimals = 0,
}: {
  label: string;
  value: number;
  suffix?: string;
  sub: string;
  spark?: number[];
  color?: string;
  decimals?: number;
}) {
  const n = useCountUp(value);
  const shown = decimals > 0 ? n.toFixed(decimals) : Math.round(n).toString();
  return (
    <Bezel coreClassName="p-5">
      <div className="flex items-start justify-between">
        <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
          {label}
        </span>
      </div>
      <div className="mt-2 flex items-end gap-1">
        <span className="text-4xl font-semibold tracking-tightest tnum text-ink">{shown}</span>
        {suffix && <span className="pb-1 text-lg font-medium text-ink-3">{suffix}</span>}
      </div>
      <div className="mt-0.5 font-mono text-2xs text-ink-3">{sub}</div>
      {spark && spark.length > 1 && (
        <div className="mt-3 -mb-1">
          <Sparkline data={spark} color={color} width={220} height={40} />
        </div>
      )}
    </Bezel>
  );
}

/* section header row — matches System.tsx's convention exactly */
function Head({ icon, children, right }: { icon: JSX.Element; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <span className="text-ink-2">{icon}</span>
        <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">{children}</span>
      </div>
      {right}
    </div>
  );
}

const outcomeSkin: Record<string, { tone: string; icon: JSX.Element; label: string }> = {
  success: { tone: "text-sev-ok bg-sev-ok/10 border-sev-ok/25", icon: <CheckCircle size={12} weight="fill" />, label: "success" },
  rolled_back: { tone: "text-sev-warn bg-sev-warn/10 border-sev-warn/25", icon: <ArrowsClockwise size={12} weight="bold" />, label: "rolled back" },
  failure: { tone: "text-sev-crit bg-sev-crit/10 border-sev-crit/25", icon: <XCircle size={12} weight="fill" />, label: "failure" },
  // Typed Record<string, ...> and read with a `?? outcomeSkin.failure` fallback, so a missing
  // entry here is invisible to tsc and would paint every escalation red — the exact operator
  // misread this state exists to prevent.
  escalated: { tone: "text-sev-attention bg-sev-attention/10 border-sev-attention/25", icon: <HandPalm size={12} weight="fill" />, label: "escalated" },
};

/** Honest AI-explainer posture, derived only from server-reported system.llm.
 *
 * Four states, not three. `last_probe` is null until someone hits Test in
 * Settings, so a correctly wired Groq endpoint used to fall through to
 * "Template (no model wired)" — which is the one thing it is definitely not,
 * and it understated the system to anyone reading the page. Configured but
 * unverified is its own answer. */
function aiExplainerState(llm: SystemInfo["llm"]) {
  if (llm.provider === "openai-compatible" && llm.last_probe?.ok) {
    return { kind: "live" as const, tone: "text-sev-ok bg-sev-ok/10 border-sev-ok/25", icon: <CheckCircle size={12} weight="fill" />, label: `LLM live · ${llm.model}` };
  }
  if (llm.endpoint_configured && llm.last_probe?.ok === false) {
    return { kind: "error" as const, tone: "text-sev-warn bg-sev-warn/10 border-sev-warn/25", icon: <Circuitry size={12} weight="light" />, label: "LLM error → template" };
  }
  if (llm.endpoint_configured) {
    return { kind: "unverified" as const, tone: "text-signal bg-signal/10 border-signal/25", icon: <Circuitry size={12} weight="light" />, label: `LLM configured · ${llm.model}` };
  }
  return { kind: "template" as const, tone: "text-ink-2 bg-surface-2 border-line-strong", icon: <Circuitry size={12} weight="light" />, label: "Template (no model wired)" };
}

export function Overview({ onView }: { onView: (v: View) => void }) {
  const { data: metrics } = useLiveData(loadMetrics, {
    alertsIngested: 0, situationsOpen: 0, noiseReductionPct: 0, mttrMinutes: 0,
    autoRemediatedPct: 0, suppressedToday: 0, approvalsPending: 0, successRate: 0, needsAttention: 0,
  } as Metrics);
  const { data: sits } = useLiveData(loadSituations, [] as Situation[]);
  const { data: outcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
  const { data: playbooks } = useData(loadPlaybooks, [] as Playbook[]);
  const { data: proposals } = useData(loadProposals, [] as ProposedPlaybook[]);
  const { data: sys } = useLiveData(loadSystem, mockSystem);

  // The KPI sparklines used to be seeded pseudo-random arrays from the mock
  // module, memoised with [] - a fabricated curve, identical every session, drawn
  // next to real numbers in live mode. There is no per-KPI history endpoint, so
  // rather than invent one they are gone: the real chart below carries the trend.

  const [metric, setMetric] = useState<string>("cpu_usage");
  const historyLoader = useCallback(() => loadMetricHistory(metric, 15), [metric]);
  const { data: history } = useLiveData(historyLoader, EMPTY_HISTORY);

  const open = useMemo(
    // Mirrors read/projection.py _OPEN exactly. The old list excluded a
    // "suppressed" status the backend never emits and counted "failed" as open,
    // so this number disagreed with the one /metrics reported beside it.
    () => sits.filter((s) => ["detected", "diagnosed", "acting", "needs_attention"].includes(s.status)),
    [sits],
  );

  const tally = useMemo(() => {
    const t: Record<RemediationResult, number> = { success: 0, rolled_back: 0, failure: 0, escalated: 0 };
    for (const o of outcomes) t[o.result] = (t[o.result] ?? 0) + 1;
    return t;
  }, [outcomes]);

  const autoCount = open.filter((s) => s.hitl_mode === "auto").length;
  const hitlCount = open.filter((s) => s.hitl_mode === "hitl").length;
  const graduated = playbooks.filter(isGraduated).length;
  const pendingProposals = proposals.filter((p) => p.status === "proposed").length;
  const explainer = aiExplainerState(sys.llm);

  return (
    <div className="space-y-6">
      {/* ── Identity + live posture ───────────────────────────────────
          Replaces the former marketing hero. An operating console opens on
          state, not on a headline: what this is, and what it is doing now. */}
      <Section>
        <div className="flex flex-col gap-4 rounded-xl border border-line-strong bg-ground-raised p-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-4">
            <span className="relative flex h-2.5 w-2.5 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-beat rounded-full bg-sev-ok/70" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-sev-ok" />
            </span>
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-ink">Incident control plane</h1>
              <p className="mt-0.5 text-sm text-ink-3">
                Detects, diagnoses and remediates production incidents. Every fix is
                reversible and recorded.
              </p>
            </div>
          </div>
          <dl className="flex flex-wrap items-center gap-x-6 gap-y-2" data-numeric>
            <PostureRow k="Correlator" v={sys.correlator_kind} />
            <PostureRow k="Remediation" v={sys.remediator_mode} />
            <PostureRow k="Bus" v={sys.bus_backend} />
            <PostureRow k="Open" v={String(open.length)} accent={open.length > 0} />
          </dl>
        </div>
      </Section>

      {/* ── The loop, with live counts ────────────────────────────────
          The explanation IS the data. Each stage carries how many things have
          passed through it, so the architecture and the current state are the
          same object and no prose is needed to describe the pipeline. */}
      <Section delay={40}>
        <Bezel coreClassName="px-5 py-5">
          <div className="flex items-center justify-between">
            <Eyebrow>Pipeline · live</Eyebrow>
            <span className="hidden font-mono text-2xs text-ink-3 sm:block">
              alert storm &rarr; one incident &rarr; verified fix
            </span>
          </div>
          <ol className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {[
              { n: "Detect", v: metrics.alertsIngested, sub: "alerts in" },
              { n: "Correlate", v: sits.length, sub: "incidents" },
              { n: "Diagnose", v: sits.filter((x) => (x.hypotheses?.length ?? 0) > 0).length, sub: "with a cause" },
              { n: "Approve", v: metrics.approvalsPending, sub: "awaiting you", accent: metrics.approvalsPending > 0 },
              { n: "Execute", v: tally.success + tally.rolled_back + tally.failure, sub: "attempted" },
              { n: "Verify", v: tally.success, sub: "healthy", ok: true },
            ].map((st, i) => (
              <li
                key={st.n}
                className="rounded-lg border border-line bg-surface px-3 py-3 transition-colors duration-200 hover:border-line-strong hover:bg-surface-2"
              >
                <span className="font-mono text-2xs uppercase tracking-[0.16em] text-ink-4">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <p className="mt-1 text-sm font-semibold tracking-tight text-ink">{st.n}</p>
                <p
                  className={`mt-2 text-2xl font-semibold tabular-nums tracking-tightest ${
                    st.accent ? "text-sev-warn" : st.ok ? "text-sev-ok" : "text-ink"
                  }`}
                >
                  {st.v}
                </p>
                <p className="mt-0.5 font-mono text-2xs text-ink-3">{st.sub}</p>
              </li>
            ))}
          </ol>
        </Bezel>
      </Section>

      {/* ── Hero KPI row ──────────────────────────────────────────────── */}
      <Section delay={60}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi
            label="Noise reduction"
            value={metrics.noiseReductionPct}
            suffix="%"
            sub={`${metrics.alertsIngested.toLocaleString()} alerts → ${metrics.situationsOpen} open`}
          />
          <Kpi
            label="Mean time to resolve"
            value={metrics.mttrMinutes}
            suffix="min"
            decimals={1}
            sub="across successful remediations"
            color="rgb(var(--sev-info))"
          />
          <Kpi
            label="Auto-remediated"
            value={metrics.autoRemediatedPct}
            suffix="%"
            sub="ran without a human"
            color="rgb(var(--sev-ok))"
          />
          <Kpi
            label="Success rate"
            value={Math.round(metrics.successRate * 100)}
            suffix="%"
            sub={metrics.needsAttention > 0 ? `${metrics.needsAttention} escalated · needs a human` : "verified healthy after fix"}
            color="rgb(var(--sev-ok))"
          />
        </div>
      </Section>

      {/* ── Metrics wall ──────────────────────────────────────────────
          Four series always on screen. The shape of a cluster is usually the
          diagnosis, and you cannot see a shape in one chart at a time. */}
      <Section delay={70}>
        <Bezel coreClassName="p-5">
          <div className="flex items-center justify-between">
            <Eyebrow>Signals · 15m</Eyebrow>
            <span className="hidden font-mono text-2xs text-ink-3 sm:block">
              scraped from every service by Prometheus
            </span>
          </div>
          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricTile metricKey="cpu_usage" label="cpu" unit="%" />
            <MetricTile metricKey="memory_usage_mb" label="memory" unit="MB" />
            <MetricTile metricKey="latency_p99_ms" label="p99 latency" unit="ms" />
            <MetricTile metricKey="meridian_error_rate" label="error rate" unit="%" scale={100} />
          </div>
        </Bezel>
      </Section>

      {/* ── Real metric history, straight from Prometheus via read-service ─ */}
      <Section delay={80}>
        <Bezel coreClassName="p-6">
          <Head
            icon={<Waveform size={16} weight="light" />}
            right={
              <div className="flex items-center gap-1">
                {METRIC_CHOICES.map((m) => (
                  <button
                    key={m.key}
                    onClick={() => setMetric(m.key)}
                    className={`rounded-full px-2.5 py-1 font-mono text-2xs transition-colors duration-200 ${
                      metric === m.key
                        ? "bg-signal/12 text-signal"
                        : "text-ink-3 hover:bg-surface-2 hover:text-ink-2"
                    }`}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            }
          >
            Live metrics
          </Head>
          <LiveChart history={history} unit={METRIC_CHOICES.find((m) => m.key === metric)?.unit ?? ""} />
          <p className="mt-2 font-mono text-2xs text-ink-3">
            {history.available
              ? `${history.series.length} services · ${Math.round((history.end - history.start) / 60)}m window · ${history.step_seconds}s resolution · scraped by Prometheus`
              : "Prometheus is not reachable from read-service — no history to draw."}
          </p>
        </Bezel>
      </Section>

      {/* ── Bento middle: live pulse (wide) + autonomy & safety (narrow) ─ */}
      <Section delay={100}>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          {/* live incident pulse */}
          <div className="lg:col-span-8">
            <Bezel coreClassName="p-6">
              <Head
                icon={<Waveform size={16} weight="light" />}
                right={
                  <span className="flex items-center gap-1.5 font-mono text-2xs text-ink-3">
                    <Broadcast size={13} weight="light" className="text-signal" /> streaming
                  </span>
                }
              >
                Live incident pulse
              </Head>

              {open.length > 0 ? (
                <div className="space-y-2.5">
                  {open.slice(0, 4).map((s) => {
                    const top = s.hypotheses[0];
                    return (
                      <button
                        key={s.id}
                        onClick={() => onView("incidents")}
                        className="block w-full text-left"
                      >
                        <div className="group rounded-lg border border-line bg-surface p-4 transition-all duration-500 ease-fluid hover:border-signal/30 hover:bg-signal/[0.04]">
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-2">
                              <SevChip sev={s.severity} />
                              <StatusChip status={s.status} />
                            </div>
                            <span className="font-mono text-2xs text-ink-3">{timeAgo(s.first_seen)}</span>
                          </div>
                          <div className="mt-2.5 flex items-center gap-2 text-sm font-medium tracking-tight text-ink">
                            <Cpu size={14} weight="light" className="text-ink-3" />
                            {s.title}
                          </div>
                          <div className="mt-2 flex items-center gap-3 font-mono text-2xs text-ink-3">
                            <span>{s.memberCount} alerts</span>
                            <span>·</span>
                            {s.suggested_runbook_id ? (
                              <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-ink-2">
                                → {s.suggested_runbook_id}
                              </span>
                            ) : (
                              <span className="text-ink-4">no matching runbook</span>
                            )}
                            {top && (
                              <span className="ml-auto flex items-center gap-1.5">
                                <span className="hidden sm:inline">confidence</span>
                                <span className="h-1 w-16 overflow-hidden rounded-full bg-surface-3">
                                  <span
                                    className="block h-full rounded-full bg-signal"
                                    style={{ width: `${top.confidence * 100}%` }}
                                  />
                                </span>
                                <span className="tnum text-ink-2">{top.confidence.toFixed(2)}</span>
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-line-strong bg-surface p-10 text-center">
                  <CheckCircle size={26} weight="light" className="mx-auto text-sev-ok" />
                  <p className="mt-2 text-sm text-ink-2">No open incidents.</p>
                  <p className="font-mono text-2xs text-ink-3">The fleet is quiet — nothing to approve.</p>
                </div>
              )}

              <div className="mt-4 flex items-center justify-between">
                <span className="font-mono text-2xs text-ink-3">
                  {open.length} active · {metrics.approvalsPending} awaiting approval
                </span>
                <CTA variant="ghost" onClick={() => onView("incidents")}>
                  Open incident workspace
                </CTA>
              </div>
            </Bezel>
          </div>

          {/* autonomy & safety */}
          <div className="lg:col-span-4">
            <Bezel coreClassName="p-6">
              <Head icon={<ShieldCheck size={16} weight="light" />}>Autonomy &amp; safety</Head>

              {/* auto vs hitl split bar */}
              <div className="rounded-lg bg-surface p-4">
                <div className="flex items-center justify-between text-2xs">
                  <span className="flex items-center gap-1.5 font-medium text-signal">
                    <Lightning size={13} weight="fill" /> Auto {autoCount}
                  </span>
                  <span className="flex items-center gap-1.5 font-medium text-ink-2">
                    HITL {hitlCount} <ShieldCheck size={13} weight="light" />
                  </span>
                </div>
                <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-surface-3">
                  <span
                    className="h-full bg-signal transition-all duration-700 ease-fluid"
                    style={{ width: `${pct(autoCount, autoCount + hitlCount)}%` }}
                  />
                  <span
                    className="h-full bg-ink-4 transition-all duration-700 ease-fluid"
                    style={{ width: `${pct(hitlCount, autoCount + hitlCount)}%` }}
                  />
                </div>
                <p className="mt-2 font-mono text-2xs leading-relaxed text-ink-3">
                  A playbook earns auto-mode only after 3 clean successes — until then a human clears
                  the gate.
                </p>
              </div>

              <div className="mt-3 grid grid-cols-2 gap-3">
                <Stat label="Approvals pending" value={metrics.approvalsPending} accent={metrics.approvalsPending > 0} />
                <Stat label="Graduated playbooks" value={graduated} />
                <Stat label="Suppressed today" value={metrics.suppressedToday} />
                <Stat label="Suppress ≥" value={0.8} decimals={2} mono />
              </div>

              {/* Was a fixed line claiming every fix is rehearsed in a sandbox
                  clone first. Sandbox mode is OFF in the live posture, so the
                  console was asserting a safeguard it was not running. State
                  whichever one is actually in force. */}
              <div className="mt-3 flex items-center gap-2 rounded-lg border border-line bg-ground px-3 py-2.5">
                <span className="flex h-6 w-6 flex-none items-center justify-center rounded-md bg-sev-ok/12 text-sev-ok">
                  <CheckCircle size={13} weight="fill" />
                </span>
                <span className="font-mono text-2xs text-ink-2">
                  {sys.sandbox_mode === "on" ? (
                    <>
                      Sandbox pre-flight{" "}
                      <span className="text-ink-3">· rehearsed on a throwaway clone first</span>
                    </>
                  ) : (
                    <>
                      Verify then roll back{" "}
                      <span className="text-ink-3">
                        · the metric that fired is re-checked, and a fix that does not hold is undone
                      </span>
                    </>
                  )}
                </span>
              </div>
            </Bezel>
          </div>
        </div>
      </Section>

      {/* ── Bento lower: AI at work (narrow) + remediation ledger (wide) ─ */}
      <Section delay={120}>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          {/* AI at work — honest about what is and isn't a live model */}
          <div className="lg:col-span-5">
            <Bezel coreClassName="p-6">
              <Head
                icon={<Sparkle size={16} weight="light" />}
                right={
                  <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-2xs ${explainer.tone}`}>
                    {explainer.icon}
                    {explainer.label}
                  </span>
                }
              >
                AI at work
              </Head>

              <div className="space-y-2.5">
                <AiRow
                  icon={<MagicWand size={15} weight="light" />}
                  title="Semantic runbook matching"
                  body="Incident symptoms are embedded and matched to each playbook's description by cosine fit — the confidence you see is that fit, not a hardcoded guess."
                  status="deterministic"
                />
                <AiRow
                  icon={<Sparkle size={15} weight="light" />}
                  title="AI-drafted runbooks"
                  body={
                    pendingProposals > 0
                      ? `${pendingProposals} draft${pendingProposals > 1 ? "s" : ""} awaiting human approval — never registered automatically.`
                      : "When RCA finds no matching runbook, a human can have one drafted — approval-gated in Governance."
                  }
                  status={pendingProposals > 0 ? "review" : "idle"}
                  onClick={() => onView("governance")}
                />
                <AiRow
                  icon={<Circuitry size={15} weight="light" />}
                  title="Root-cause explanations"
                  body={
                    explainer.kind === "live"
                      ? "A language model narrates why each incident was diagnosed as it was."
                      : explainer.kind === "unverified"
                        ? "An endpoint is wired and will be used for explanations. Run Test in Settings to confirm it answers."
                        : "Plain-language explanations are template-generated until a model endpoint is wired in Settings."
                  }
                  status={explainer.kind === "live" ? "live" : explainer.kind === "unverified" ? "review" : "template"}
                  onClick={() => onView("settings")}
                />
              </div>

              <p className="mt-3 font-mono text-2xs leading-relaxed text-ink-3">
                The decisions — which runbook, whether it worked, whether to suppress — stay
                deterministic and auditable. AI advises and drafts; humans and policy decide.
              </p>
            </Bezel>
          </div>

          {/* remediation ledger */}
          <div className="lg:col-span-7">
            <Bezel coreClassName="p-6">
              <Head
                icon={<Gauge size={16} weight="light" />}
                right={
                  <span className="flex items-center gap-2 font-mono text-2xs">
                    <span className="text-sev-ok">{tally.success}✓</span>
                    <span className="text-sev-warn">{tally.rolled_back}↺</span>
                    <span className="text-sev-crit">{tally.failure}✕</span>
                    <span className="text-sev-attention">{tally.escalated}⤴</span>
                  </span>
                }
              >
                Remediation ledger
              </Head>

              {outcomes.length > 0 ? (
                <div className="space-y-1">
                  {outcomes.slice(0, 7).map((o, i) => {
                    const skin = outcomeSkin[o.result] ?? outcomeSkin.failure;
                    return (
                      <div
                        key={i}
                        className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-surface"
                      >
                        <span className={`inline-flex flex-none items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-2xs ${skin.tone}`}>
                          {skin.icon}
                          <span className="hidden sm:inline">{skin.label}</span>
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{o.playbook_id || <span className="text-ink-3">no runbook — needs a human</span>}</span>
                        <span className="hidden font-mono text-2xs text-ink-3 sm:inline">{o.service}</span>
                        <span className="font-mono text-2xs text-ink-3">{timeAgo(o.ts)}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed border-line-strong p-8 text-center font-mono text-2xs text-ink-3">
                  No remediations recorded yet.
                </div>
              )}

              <div className="mt-4 flex items-center justify-between">
                <span className="font-mono text-2xs text-ink-3">last {Math.min(7, outcomes.length)} outcomes</span>
                <CTA variant="ghost" onClick={() => onView("governance")}>
                  Governance &amp; audit
                </CTA>
              </div>
            </Bezel>
          </div>
        </div>
      </Section>

      {/* ── Metric coverage — the USE+RED surface + per-metric lifecycle ── */}
      <Section delay={140}>
        <Bezel coreClassName="p-6">
          <Head
            icon={<Waveform size={16} weight="light" />}
            right={<span className="font-mono text-2xs text-ink-3">USE + RED · {METRIC_FAMILIES.length} families</span>}
          >
            Metric coverage
          </Head>
          <p className="mb-4 max-w-[70ch] font-mono text-2xs leading-relaxed text-ink-3">
            Every metric is detected by the rule that fits its kind, routed to a runbook by family,
            and — after a fix — verified on the metric that actually fired (not cpu by default).
            Detect → diagnose → verify, per metric.
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {METRIC_FAMILIES.map((m) => (
              <div key={m.name} className="rounded-lg border border-line bg-surface p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-2xs font-medium text-ink">{m.name}</span>
                  <span className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.625rem] uppercase tracking-wider text-ink-3">{m.kind}</span>
                </div>
                <div className="mt-1.5 flex items-center gap-1.5 font-mono text-2xs text-ink-3">
                  <span className="text-ink-3">detect</span>
                  <span className="text-ink-4">·</span>
                  <span className="text-signal-dim">{m.detect}</span>
                  <span className="ml-auto rounded bg-signal/[0.08] px-1.5 py-0.5 text-signal-dim">→ {m.runbook}</span>
                </div>
              </div>
            ))}
          </div>
        </Bezel>
      </Section>

      {/* ── Fleet strip ───────────────────────────────────────────────── */}
      <Section delay={160}>
        <Bezel coreClassName="p-6">
          <Head
            icon={<Pulse size={16} weight="light" />}
            right={<span className="font-mono text-2xs text-ink-3">6 services</span>}
          >
            Service fleet
          </Head>
          <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
            {FLEET.map((svc) => (
              <div key={svc.name} className="rounded-lg border border-line bg-surface p-3.5">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium tracking-tight text-ink">{svc.name}</span>
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-beat rounded-full bg-sev-ok/70" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-sev-ok" />
                  </span>
                </div>
                <div className="mt-1 font-mono text-2xs text-ink-3">:{svc.port}</div>
                <div className="mt-2 font-mono text-2xs text-ink-3">{svc.role}</div>
              </div>
            ))}
          </div>
        </Bezel>
      </Section>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   One always-on metric. The wall of these is the point: a single selectable
   chart makes you go looking, whereas four running side by side let you see a
   correlation (flat CPU beside climbing memory says "leak") without choosing
   anything first. Each tile owns its own poll.
--------------------------------------------------------------------------- */
function MetricTile({
  metricKey,
  label,
  unit,
  scale = 1,
}: {
  metricKey: string;
  label: string;
  unit: string;
  /** Multiplier applied to the headline figure only, for metrics stored as a
   *  fraction but read as a percentage (meridian_error_rate). */
  scale?: number;
}) {
  const loader = useCallback(() => loadMetricHistory(metricKey, 15), [metricKey]);
  const { data } = useLiveData(loader, { ...EMPTY_HISTORY, metric: metricKey });

  // Highest current reading across services: on a wall of four, the question is
  // always "is anything hot", not "what is the average".
  const peak = useMemo(() => {
    let v: number | null = null;
    let who = "";
    for (const srs of data.series ?? []) {
      const pts = srs.points ?? [];
      if (!pts.length) continue;
      // points are [epochSeconds, value] tuples
      const last = pts[pts.length - 1][1];
      if (v === null || last > v) {
        v = last;
        who = srs.service;
      }
    }
    return { value: v, service: who };
  }, [data]);

  const shown = peak.value === null ? null : peak.value * scale;

  return (
    <div className="rounded-lg border border-line bg-ground p-3.5 transition-colors duration-200 hover:border-line-strong">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-2xs uppercase tracking-[0.14em] text-ink-4">{label}</span>
        <span className="font-mono text-sm tabular-nums text-ink">
          {shown === null ? "—" : `${compactNum(shown)}${unit}`}
        </span>
      </div>
      <div className="mt-2">
        <LiveChart history={data} unit={unit} height={92} maxSeries={5} compact />
      </div>
      <div className="mt-1.5 truncate font-mono text-2xs text-ink-4">
        {peak.service ? `peak · ${peak.service}` : "waiting for samples"}
      </div>
    </div>
  );
}

/** Headline figure for a tile: enough significant digits to see a change, never
 *  so many that the four tiles stop lining up. An error rate of 0.0034 used to
 *  render as "0.00". */
function compactNum(v: number): string {
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

/* ── small local building blocks ──────────────────────────────────────── */

/* One governing setting from /system. Rendered as a dt/dd pair because the
   identity band is a definition list: these are labelled values, not rows. */
function PostureRow({ k, v, accent = false }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="font-mono text-2xs uppercase tracking-[0.14em] text-ink-4">{k}</dt>
      <dd className={`font-mono text-xs ${accent ? "text-sev-warn" : "text-ink-2"}`}>{v}</dd>
    </div>
  );
}

function Stat({
  label, value, decimals = 0, accent = false, mono = false,
}: { label: string; value: number; decimals?: number; accent?: boolean; mono?: boolean }) {
  const shown = decimals > 0 ? value.toFixed(decimals) : value.toString();
  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2.5">
      <div className={`${mono ? "font-mono text-xl" : "text-2xl"} font-semibold tracking-tight tnum ${accent ? "text-sev-warn" : "text-ink"}`}>
        {shown}
      </div>
      <div className="mt-0.5 text-2xs font-medium uppercase tracking-[0.1em] text-ink-3">{label}</div>
    </div>
  );
}

function AiRow({
  icon, title, body, status, onClick,
}: {
  icon: JSX.Element;
  title: string;
  body: string;
  status: "deterministic" | "review" | "idle" | "live" | "template";
  onClick?: () => void;
}) {
  const badge: Record<string, string> = {
    deterministic: "text-signal bg-signal/10 border-signal/20",
    review: "text-sev-warn bg-sev-warn/10 border-sev-warn/25",
    idle: "text-ink-3 bg-surface-2 border-line-strong",
    live: "text-sev-ok bg-sev-ok/10 border-sev-ok/25",
    template: "text-ink-2 bg-surface-2 border-line-strong",
  };
  const Wrap: React.ElementType = onClick ? "button" : "div";
  return (
    <Wrap
      onClick={onClick}
      className={`flex w-full items-start gap-3 rounded-lg border border-line bg-surface p-3.5 text-left ${
        onClick ? "transition-colors duration-300 hover:bg-surface-2" : ""
      }`}
    >
      <span className="mt-0.5 flex h-8 w-8 flex-none items-center justify-center rounded-xl bg-signal/[0.08] text-signal">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium tracking-tight text-ink">{title}</span>
          <span className={`rounded-full border px-1.5 py-0.5 font-mono text-[0.625rem] uppercase tracking-wider ${badge[status]}`}>
            {status}
          </span>
        </div>
        <p className="mt-1 font-mono text-2xs leading-relaxed text-ink-3">{body}</p>
      </div>
    </Wrap>
  );
}

function pct(a: number, total: number): number {
  return total > 0 ? Math.round((a / total) * 100) : 0;
}

/* The six services and their roles/ports — the shipped topology (mirrors
   mock.ts `services`, condensed for the strip). The Overview shows the fleet
   as a health glance; the System view has the authoritative live detail. */
const FLEET = [
  { name: "ingestion", port: 8001, role: "normalize · dedup" },
  { name: "correlation", port: 8002, role: "cluster → Situation" },
  { name: "rca", port: 8003, role: "enrich · rank" },
  { name: "action", port: 8004, role: "approve · execute" },
  { name: "governance", port: 8005, role: "RBAC · audit" },
  { name: "feedback", port: 8006, role: "label · retrain" },
] as const;

/* The USE+RED metric families (Phase 1) with their detection rule (Phase 2) and
   the runbook their family routes to (Phase 3). Recovery is then verified on
   the firing metric (Phase 4). The whole arc, per metric. */
const METRIC_FAMILIES = [
  { name: "cpu_usage", kind: "USE · util", detect: "saturation ≥ 90", runbook: "scale-service" },
  { name: "memory_usage_mb", kind: "USE · util", detect: "z-score", runbook: "restart-pod" },
  { name: "db_pool_in_use", kind: "USE · util", detect: "z-score", runbook: "restart-pod" },
  { name: "queue_depth", kind: "USE · sat", detect: "z-score", runbook: "scale-service" },
  { name: "meridian_error_rate", kind: "RED · errors", detect: "ratio ≥ 2%", runbook: "restart-pod" },
  { name: "latency_p99_ms", kind: "RED · duration", detect: "ceiling 500ms / z", runbook: "scale-service" },
  { name: "request_rate", kind: "RED · rate", detect: "z-score", runbook: "scale-service" },
] as const;
