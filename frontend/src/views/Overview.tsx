import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, CheckCircle, GraduationCap, WarningCircle } from "@phosphor-icons/react";
import { Bezel, Eyebrow, Sparkline } from "../components/primitives";
import { metrics as mockMetrics, series, services } from "../data/mock";
import { loadMetrics, loadOutcomes, loadPlaybooks } from "../data/source";
import { useLiveData } from "../hooks/useLiveData";
import { timeAgo } from "../components/primitives";
import { Reveal as Section } from "../hooks/useReveal";
import type { Metrics, OutcomeReason, OutcomeRow, Playbook, ServiceHealth } from "../data/types";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

const READ_URL = import.meta.env.VITE_READ_URL ?? "http://localhost:8007";
const GOV_URL = import.meta.env.VITE_GOV_URL ?? "http://localhost:8005";

/** Services whose base URL the browser actually knows (only these two are
 * configured via VITE_*_URL). The remaining four run behind ports the
 * frontend has no env var for, so their live health can't be pinged
 * directly from the browser — they render as "degraded/unknown" below
 * rather than faked as healthy. */
const PINGABLE_SERVICES: { name: string; url: string }[] = [
  { name: "read", url: READ_URL },
  { name: "governance", url: GOV_URL },
];

const MAX_BUFFER = 40;

/** Sparkline needs >=2 points (it divides by data.length - 1). Pad a short
 * live buffer into a flat line instead of crashing on NaN coordinates. */
function sparkSeries(buffer: number[], fallback: number): number[] {
  if (buffer.length >= 2) return buffer;
  const v = buffer.length === 1 ? buffer[0] : fallback;
  return [v, v];
}

const reasonTone: Record<OutcomeReason, string> = {
  healthy: "text-sev-ok",
  "unhealthy:rolled-back": "text-sev-warn",
  "execute-failed": "text-sev-crit",
  "denied:rbac": "text-sev-crit",
  "refused:not-reversible": "text-sev-warn",
  "aborted:rejected": "text-sev-warn",
  "aborted:timeout": "text-sev-warn",
  "skipped:disabled": "text-ink-3",
  "skipped:no-playbook": "text-ink-3",
};

function MiniStat({ label, value, unit, delta, up, spark, color }: {
  label: string; value: string; unit?: string; delta?: string; up?: boolean; spark: number[]; color: string;
}) {
  return (
    <Bezel coreClassName="p-5 h-full flex flex-col justify-between">
      <div className="flex items-start justify-between">
        <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">{label}</span>
        {delta && (
          <span className={`flex items-center gap-0.5 font-mono text-2xs ${up ? "text-sev-ok" : "text-sev-crit"}`}>
            {up ? <ArrowUp size={11} weight="bold" /> : <ArrowDown size={11} weight="bold" />}
            {delta}
          </span>
        )}
      </div>
      <div className="mt-3 flex items-end justify-between gap-2">
        <div className="text-3xl font-semibold tracking-tightest tnum">
          {value}
          {unit && <span className="ml-0.5 text-base font-medium text-ink-3">{unit}</span>}
        </div>
        <Sparkline data={spark} color={color} width={104} height={40} />
      </div>
    </Bezel>
  );
}

export function Overview() {
  const { data: outcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
  const { data: playbooks } = useLiveData(loadPlaybooks, [] as Playbook[]);
  const { data: metrics } = useLiveData(loadMetrics, mockMetrics);

  // Rolling client-side buffer of real metric samples, fed by every
  // useLiveData(loadMetrics) update. Live-mode-only; mock mode keeps using
  // data/mock's series() generator untouched.
  const bufferRef = useRef<Metrics[]>([]);
  const [history, setHistory] = useState<Metrics[]>([]);
  useEffect(() => {
    if (!LIVE) return;
    bufferRef.current = [...bufferRef.current, metrics].slice(-MAX_BUFFER);
    setHistory(bufferRef.current);
  }, [metrics]);

  const noiseHistory = history.map((m) => m.noiseReductionPct);
  const mttrHistory = history.map((m) => m.mttrMinutes);
  const autoRemHistory = history.map((m) => m.autoRemediatedPct);

  // Fleet health, live mode: ping the two services whose base URL the
  // browser knows (read, governance — both expose an always-auth-exempt
  // /health). The other four services run on ports the frontend has no
  // VITE_*_URL for, so they're reported "unknown" rather than invented as
  // healthy — pinging them would require guessing ports/CORS.
  const [liveHealth, setLiveHealth] = useState<Record<string, "ok" | "down">>({});
  useEffect(() => {
    if (!LIVE) return;
    let alive = true;
    const check = async () => {
      const results = await Promise.all(
        PINGABLE_SERVICES.map(async ({ name, url }) => {
          try {
            const r = await fetch(`${url}/health`);
            return [name, r.ok ? "ok" : "down"] as const;
          } catch {
            return [name, "down"] as const;
          }
        }),
      );
      if (!alive) return;
      setLiveHealth(Object.fromEntries(results));
    };
    check();
    const id = window.setInterval(check, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  // Services with no known base URL render as "degraded" (⚠ unknown), never
  // as a fabricated "ok" — that's the mock-leak bug this fixes.
  const liveFleet: ServiceHealth[] = services.map((s) => {
    const known = liveHealth[s.name];
    return {
      ...s,
      status: known ?? "degraded",
      throughput: known === "ok" ? s.throughput : 0,
    };
  });
  const fleet = LIVE ? liveFleet : services;
  const fleetHealthyCount = fleet.filter((s) => s.status === "ok").length;

  return (
    <div className="space-y-5">
      <Section>
        <Eyebrow>
          <span className="h-1.5 w-1.5 rounded-full bg-signal" /> Center of Excellence · live
        </Eyebrow>
        <h1 className="mt-4 max-w-[16ch] text-4xl font-semibold tracking-tightest sm:text-5xl">
          Your incident surface, <span className="text-signal">and it&apos;s learning.</span>
        </h1>
        <p className="mt-3 max-w-[58ch] text-base leading-relaxed text-ink-2">
          Six services collapse the noise, diagnose the cause, remediate under governance, and feed every
          outcome back so proven-benign incidents stop paging. Here is the last 24 hours.
        </p>
      </Section>

      {/* Asymmetrical bento */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-12 md:auto-rows-[minmax(0,auto)]">
        {/* hero metric — noise reduction */}
        <Section className="md:col-span-7 md:row-span-2">
          <Bezel glow coreClassName="relative overflow-hidden p-7 h-full">
            <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-signal/[0.06] blur-3xl" />
            <div className="relative flex h-full flex-col">
              <div className="flex items-center justify-between">
                <span className="text-2xs font-medium uppercase tracking-[0.16em] text-ink-3">Alert noise reduction · today</span>
                <span className="rounded-full border border-sev-ok/25 bg-sev-ok/10 px-2.5 py-0.5 font-mono text-2xs text-sev-ok">{metrics.noiseReductionPct >= 80 ? "on target" : `${metrics.noiseReductionPct}%`}</span>
              </div>
              <div className="mt-6 flex items-end gap-3">
                <div className="text-[5.5rem] font-semibold leading-[0.9] tracking-tightest tnum">{metrics.noiseReductionPct}<span className="text-4xl text-ink-3">%</span></div>
              </div>
              <p className="mt-2 max-w-[38ch] text-sm text-ink-2">
                <span className="text-ink">{metrics.alertsIngested.toLocaleString()} raw alerts</span> collapsed into a handful of Situations. <span className="text-signal">{metrics.suppressedToday} storms</span> were suppressed entirely — the system had already proven their fix.
              </p>
              <div className="mt-auto pt-6">
                <Sparkline
                  data={LIVE ? sparkSeries(noiseHistory, metrics.noiseReductionPct) : series(40, 62, 0.7, 11)}
                  color="#0071E3"
                  width={520}
                  height={92}
                />
                <div className="mt-1 flex justify-between font-mono text-2xs text-ink-3"><span>00:00</span><span>target band 80–95%</span><span>now</span></div>
              </div>
            </div>
          </Bezel>
        </Section>

        {/* two mini stats top-right */}
        <Section className="md:col-span-5">
          <MiniStat
            label="MTTR"
            value={metrics.mttrMinutes.toFixed(1)}
            unit="min"
            delta={LIVE ? undefined : "−41%"}
            up={!LIVE}
            spark={LIVE ? sparkSeries(mttrHistory, metrics.mttrMinutes) : series(24, 14, -0.32, 3)}
            color="#34C759"
          />
        </Section>
        <Section className="md:col-span-5">
          <MiniStat
            label="Auto-remediated"
            value={`${metrics.autoRemediatedPct}`}
            unit="%"
            delta={LIVE ? undefined : "+6"}
            up={!LIVE}
            spark={LIVE ? sparkSeries(autoRemHistory, metrics.autoRemediatedPct) : series(24, 22, 0.6, 9)}
            color="#5E5CE6"
          />
        </Section>

        {/* service health strip */}
        <Section className="md:col-span-5">
          <Bezel coreClassName="p-5">
            <div className="flex items-center justify-between">
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Fleet health</span>
              <span className={`font-mono text-2xs ${fleetHealthyCount === fleet.length ? "text-sev-ok" : "text-sev-warn"}`}>
                {fleetHealthyCount}/{fleet.length} healthy
              </span>
            </div>
            <div className="mt-4 space-y-2.5">
              {fleet.map((s) => (
                <div key={s.name} className="flex items-center gap-3">
                  <span className={`h-1.5 w-1.5 flex-none rounded-full ${s.status === "ok" ? "bg-sev-ok" : s.status === "degraded" ? "bg-sev-warn" : "bg-sev-crit"}`} />
                  <span className="w-24 text-sm text-ink">{s.name}</span>
                  <span className="hidden flex-1 truncate font-mono text-2xs text-ink-3 sm:block">{s.role}</span>
                  <span className="font-mono text-2xs text-ink-3">:{s.port}</span>
                  <span className="w-16 text-right font-mono text-2xs text-ink-2 tnum">{s.throughput}/m</span>
                </div>
              ))}
            </div>
          </Bezel>
        </Section>

        {/* live outcomes ticker */}
        <Section className="md:col-span-7">
          <Bezel coreClassName="p-5">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">remediation.outcomes · live</span>
              <span className="font-mono text-2xs text-ink-3">success rate {(metrics.successRate * 100).toFixed(0)}%</span>
            </div>
            <div className="space-y-1.5">
              {outcomes.slice(0, 6).map((o, i) => (
                <div key={i} className="flex items-center gap-3 rounded-lg px-2 py-1.5 font-mono text-2xs transition-colors hover:bg-black/[0.03]">
                  <span className="text-ink-3">{timeAgo(o.ts)}</span>
                  <span className="w-28 truncate text-ink-2">{o.service}</span>
                  <span className="w-32 truncate text-ink-3">{o.playbook_id}</span>
                  <span className={`ml-auto ${reasonTone[o.reason]}`}>{o.reason}</span>
                </div>
              ))}
            </div>
          </Bezel>
        </Section>

        {/* graduation status */}
        <Section className="md:col-span-12">
          <Bezel coreClassName="p-5">
            <div className="mb-4 flex items-center gap-2">
              <GraduationCap size={17} weight="light" className="text-signal" />
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Playbook autonomy · evidence-based graduation (≥3 clean successes)</span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {playbooks.map((p) => {
                const clean = p.rollbacks === 0 && p.failures === 0;
                const pct = Math.min(100, (p.successes / 3) * 100);
                return (
                  <div key={p.id} className="rounded-2xl border border-black/[0.06] bg-black/[0.02] p-4">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">{p.name}</span>
                      {p.graduated ? (
                        <span className="flex items-center gap-1 rounded-full bg-signal/10 px-2 py-0.5 font-mono text-2xs text-signal-dim"><CheckCircle size={12} weight="fill" /> auto</span>
                      ) : (
                        <span className="rounded-full bg-black/[0.05] px-2 py-0.5 font-mono text-2xs text-ink-2">hitl</span>
                      )}
                    </div>
                    <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-black/[0.08]">
                      <div
                        className={`bar-fill h-full rounded-full ${p.graduated ? "bg-signal" : clean ? "bg-sev-ok" : "bg-sev-warn"}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="mt-2 flex items-center gap-3 font-mono text-2xs text-ink-3">
                      <span className="text-sev-ok">{p.successes}✓</span>
                      {p.rollbacks > 0 && <span className="text-sev-warn flex items-center gap-1"><WarningCircle size={11} />{p.rollbacks} rollback</span>}
                      {p.failures > 0 && <span className="text-sev-crit">{p.failures} fail</span>}
                      {clean && !p.graduated && <span className="ml-auto text-ink-2">{3 - p.successes > 0 ? `${3 - p.successes} more to graduate` : "ready"}</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </Bezel>
        </Section>
      </div>
    </div>
  );
}
