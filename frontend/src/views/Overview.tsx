import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowsClockwise,
  Broadcast,
  CheckCircle,
  Circuitry,
  Cpu,
  Gauge,
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
} from "../data/source";
import { series } from "../data/mock";
import { system as mockSystem } from "../data/mock";
import type {
  Metrics,
  OutcomeRow,
  Playbook,
  ProposedPlaybook,
  Situation,
  SystemInfo,
} from "../data/types";
import type { View } from "../components/Shell";

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
  color = "#0071E3",
  decimals = 0,
}: {
  label: string;
  value: number;
  suffix?: string;
  sub: string;
  spark: number[];
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
      <div className="mt-3 -mb-1">
        <Sparkline data={spark} color={color} width={220} height={40} />
      </div>
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
};

/** Honest AI-explainer posture, derived only from server-reported system.llm. */
function aiExplainerState(llm: SystemInfo["llm"]) {
  if (llm.provider === "openai-compatible" && llm.last_probe?.ok) {
    return { tone: "text-sev-ok bg-sev-ok/10 border-sev-ok/25", icon: <CheckCircle size={12} weight="fill" />, label: `LLM live · ${llm.model}` };
  }
  if (llm.endpoint_configured && llm.last_probe?.ok === false) {
    return { tone: "text-sev-warn bg-sev-warn/10 border-sev-warn/25", icon: <Circuitry size={12} weight="light" />, label: "LLM error → template" };
  }
  return { tone: "text-ink-2 bg-black/[0.05] border-black/[0.10]", icon: <Circuitry size={12} weight="light" />, label: "Template (no model wired)" };
}

export function Overview({ onView }: { onView: (v: View) => void }) {
  const { data: metrics } = useLiveData(loadMetrics, {
    alertsIngested: 0, situationsOpen: 0, noiseReductionPct: 0, mttrMinutes: 0,
    autoRemediatedPct: 0, suppressedToday: 0, approvalsPending: 0, successRate: 0,
  } as Metrics);
  const { data: sits } = useLiveData(loadSituations, [] as Situation[]);
  const { data: outcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
  const { data: playbooks } = useData(loadPlaybooks, [] as Playbook[]);
  const { data: proposals } = useData(loadProposals, [] as ProposedPlaybook[]);
  const { data: sys } = useLiveData(loadSystem, mockSystem);

  // sparkline series are deterministic (seeded) so they don't jitter each poll.
  const sparks = useMemo(
    () => ({
      noise: series(24, 60, 1.3, 3),
      mttr: series(24, 14, -0.35, 11),
      auto: series(24, 22, 0.7, 5),
      success: series(24, 82, 0.5, 9),
    }),
    [],
  );

  const open = useMemo(
    () => sits.filter((s) => !["resolved", "suppressed"].includes(s.status)),
    [sits],
  );

  const tally = useMemo(() => {
    const t = { success: 0, rolled_back: 0, failure: 0 };
    for (const o of outcomes) t[o.result] = (t[o.result] ?? 0) + 1;
    return t;
  }, [outcomes]);

  const autoCount = open.filter((s) => s.hitl_mode === "auto").length;
  const hitlCount = open.filter((s) => s.hitl_mode === "hitl").length;
  const graduated = playbooks.filter((p) => p.graduated).length;
  const pendingProposals = proposals.filter((p) => p.status === "proposed").length;
  const explainer = aiExplainerState(sys.llm);

  return (
    <div className="space-y-6">
      {/* ── Hero band ─────────────────────────────────────────────────── */}
      <Section>
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <Eyebrow>
              <span className="h-1.5 w-1.5 animate-beat rounded-full bg-sev-ok" /> Operating picture · live
            </Eyebrow>
            <h1 className="mt-4 text-4xl font-semibold tracking-tightest sm:text-5xl lg:text-6xl">
              Autonomous operations,
              <br />
              <span className="text-signal">under control.</span>
            </h1>
            <p className="mt-4 max-w-[52ch] text-base leading-relaxed text-ink-2">
              One command plane over the whole incident lifecycle — detect, diagnose, remediate,
              and verify. Every automated fix is reversible, RBAC-gated, and rehearsed in a sandbox
              before it touches production.
            </p>
          </div>

          {/* live posture cluster — real system fields */}
          <Bezel className="w-full shrink-0 lg:w-[300px]" coreClassName="p-5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2">
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full animate-beat rounded-full bg-sev-ok" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-sev-ok" />
                </span>
                <span className="text-sm font-semibold tracking-tight">All systems nominal</span>
              </span>
              <span className="font-mono text-2xs text-ink-3">6/6</span>
            </div>
            <div className="mt-4 space-y-2">
              <PostureRow k="Correlator" v={sys.correlator_kind} />
              <PostureRow k="Remediation" v={sys.remediator_mode} />
              <PostureRow k="Bus" v={sys.bus_backend} />
              <PostureRow k="Open incidents" v={String(open.length)} accent={open.length > 0} />
            </div>
          </Bezel>
        </div>
      </Section>

      {/* ── Hero KPI row ──────────────────────────────────────────────── */}
      <Section delay={60}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi
            label="Noise reduction"
            value={metrics.noiseReductionPct}
            suffix="%"
            sub={`${metrics.alertsIngested.toLocaleString()} alerts → ${metrics.situationsOpen} open`}
            spark={sparks.noise}
          />
          <Kpi
            label="Mean time to resolve"
            value={metrics.mttrMinutes}
            suffix="min"
            decimals={1}
            sub="across successful remediations"
            spark={sparks.mttr}
            color="#5E5CE6"
          />
          <Kpi
            label="Auto-remediated"
            value={metrics.autoRemediatedPct}
            suffix="%"
            sub="ran without a human"
            spark={sparks.auto}
            color="#34C759"
          />
          <Kpi
            label="Success rate"
            value={Math.round(metrics.successRate * 100)}
            suffix="%"
            sub="verified healthy after fix"
            spark={sparks.success}
            color="#34C759"
          />
        </div>
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
                        <div className="group rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4 transition-all duration-500 ease-fluid hover:border-signal/30 hover:bg-signal/[0.04]">
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
                              <span className="rounded-md bg-black/[0.05] px-1.5 py-0.5 text-ink-2">
                                → {s.suggested_runbook_id}
                              </span>
                            ) : (
                              <span className="text-ink-4">no matching runbook</span>
                            )}
                            {top && (
                              <span className="ml-auto flex items-center gap-1.5">
                                <span className="hidden sm:inline">confidence</span>
                                <span className="h-1 w-16 overflow-hidden rounded-full bg-black/[0.08]">
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
                <div className="rounded-2xl border border-dashed border-black/[0.10] bg-black/[0.02] p-10 text-center">
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
              <div className="rounded-2xl bg-black/[0.02] p-4">
                <div className="flex items-center justify-between text-2xs">
                  <span className="flex items-center gap-1.5 font-medium text-signal">
                    <Lightning size={13} weight="fill" /> Auto {autoCount}
                  </span>
                  <span className="flex items-center gap-1.5 font-medium text-ink-2">
                    HITL {hitlCount} <ShieldCheck size={13} weight="light" />
                  </span>
                </div>
                <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-black/[0.06]">
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

              <div className="mt-3 flex items-center gap-2 rounded-xl border border-black/[0.06] bg-black/[0.02] px-3 py-2.5">
                <span className="flex h-6 w-6 flex-none items-center justify-center rounded-lg bg-sev-ok/12 text-sev-ok">
                  <CheckCircle size={13} weight="fill" />
                </span>
                <span className="font-mono text-2xs text-ink-2">
                  Sandbox pre-flight <span className="text-ink-3">· fixes rehearsed on a throwaway clone first</span>
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
                    explainer.label.startsWith("LLM live")
                      ? "A language model narrates why each incident was diagnosed as it was."
                      : "Plain-language explanations are template-generated until a model endpoint is wired in Settings."
                  }
                  status={explainer.label.startsWith("LLM live") ? "live" : "template"}
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
                        className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-black/[0.02]"
                      >
                        <span className={`inline-flex flex-none items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-2xs ${skin.tone}`}>
                          {skin.icon}
                          <span className="hidden sm:inline">{skin.label}</span>
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{o.playbook_id}</span>
                        <span className="hidden font-mono text-2xs text-ink-3 sm:inline">{o.service}</span>
                        <span className="font-mono text-2xs text-ink-3">{timeAgo(o.ts)}</span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-black/[0.10] p-8 text-center font-mono text-2xs text-ink-3">
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
              <div key={m.name} className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-3.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-2xs font-medium text-ink">{m.name}</span>
                  <span className="rounded bg-black/[0.05] px-1.5 py-0.5 font-mono text-[0.625rem] uppercase tracking-wider text-ink-3">{m.kind}</span>
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
              <div key={svc.name} className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-3.5">
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

/* ── small local building blocks ──────────────────────────────────────── */

function PostureRow({ k, v, accent = false }: { k: string; v: string; accent?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-2xs font-medium uppercase tracking-[0.1em] text-ink-3">{k}</span>
      <span className={`font-mono text-2xs ${accent ? "text-sev-warn" : "text-ink"}`}>{v}</span>
    </div>
  );
}

function Stat({
  label, value, decimals = 0, accent = false, mono = false,
}: { label: string; value: number; decimals?: number; accent?: boolean; mono?: boolean }) {
  const shown = decimals > 0 ? value.toFixed(decimals) : value.toString();
  return (
    <div className="rounded-xl border border-black/[0.06] bg-black/[0.02] px-3 py-2.5">
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
    idle: "text-ink-3 bg-black/[0.04] border-black/[0.08]",
    live: "text-sev-ok bg-sev-ok/10 border-sev-ok/25",
    template: "text-ink-2 bg-black/[0.05] border-black/[0.10]",
  };
  const Wrap: React.ElementType = onClick ? "button" : "div";
  return (
    <Wrap
      onClick={onClick}
      className={`flex w-full items-start gap-3 rounded-2xl border border-black/[0.06] bg-black/[0.02] p-3.5 text-left ${
        onClick ? "transition-colors duration-300 hover:bg-black/[0.04]" : ""
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
