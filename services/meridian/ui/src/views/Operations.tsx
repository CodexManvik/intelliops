import { useState } from "react";
import {
  clearFault,
  deploy,
  injectFault,
  type FaultSpec,
  type FaultType,
  type MeridianService,
} from "../data/api";
import StatusPill from "../components/StatusPill";

const SERVICES: MeridianService[] = ["gateway", "validation", "aggregation", "reporting"];
const FAULT_TYPES: FaultType[] = [
  "saturation",
  "error",
  "latency",
  "crash",
  "memory_leak",
  "traffic_surge",
  "dependency_outage",
  "db_exhaustion",
];

interface Preset {
  id: string;
  label: string;
  description: string;
  service: MeridianService;
  spec: FaultSpec;
  deployFirst?: boolean;
}

const PRESETS: Preset[] = [
  {
    id: "aggregation-saturated",
    label: "Aggregation saturated",
    description: "CPU saturation on the aggregation service.",
    service: "aggregation",
    spec: { type: "saturation" },
  },
  {
    id: "report-slow",
    label: "Report slow",
    description: "Elevated latency on the reporting service.",
    service: "reporting",
    spec: { type: "latency" },
  },
  {
    id: "validation-errors",
    label: "Validation errors",
    description: "Elevated error rate (50%) on validation.",
    service: "validation",
    spec: { type: "error", magnitude: 0.5 },
  },
  {
    id: "bad-gateway-deploy",
    label: "Bad gateway deploy",
    description: "Deploy marker on gateway, then saturation fault.",
    service: "gateway",
    spec: { type: "saturation" },
    deployFirst: true,
  },
  {
    id: "aggregation-memory-leak",
    label: "Memory leak (gradual)",
    description:
      "Gradually ramps memory usage toward OOM on aggregation over the fault duration — no other metric moves.",
    service: "aggregation",
    spec: { type: "memory_leak" },
  },
  {
    id: "gateway-traffic-surge",
    label: "Traffic surge",
    description:
      "More legitimate request volume than gateway has capacity for: request rate, CPU, saturation, and queue depth all climb together.",
    service: "gateway",
    spec: { type: "traffic_surge" },
  },
  {
    id: "validation-dependency-outage",
    label: "Dependency outage",
    description:
      "A downstream dependency of validation is down: errors and p99 latency spike, but CPU stays flat — it's not a capacity problem.",
    service: "validation",
    spec: { type: "dependency_outage" },
  },
  {
    id: "reporting-db-exhaustion",
    label: "DB pool exhaustion",
    description:
      "Reporting's DB connection pool fills up: in-use connections hit the max and latency rises as requests queue for a connection.",
    service: "reporting",
    spec: { type: "db_exhaustion" },
  },
];

interface ActiveFault {
  service: MeridianService;
  spec: FaultSpec;
  label: string;
  startedAt: number;
}

type Busy = { kind: "inject" | "clear"; presetId?: string } | null;

export default function Operations() {
  const [activeFault, setActiveFault] = useState<ActiveFault | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [log, setLog] = useState<string[]>([]);

  // Custom composer state
  const [customService, setCustomService] = useState<MeridianService>("aggregation");
  const [customType, setCustomType] = useState<FaultType>("saturation");
  const [customMagnitude, setCustomMagnitude] = useState(1);
  const [customDuration, setCustomDuration] = useState(60);
  const [customDeploy, setCustomDeploy] = useState(false);

  const guardActive = activeFault !== null;
  const inFlight = busy !== null;
  const injectDisabled = guardActive || inFlight;

  const appendLog = (line: string) => {
    const ts = new Date().toLocaleTimeString();
    setLog((prev) => [`${ts}  ${line}`, ...prev].slice(0, 12));
  };

  const runPreset = async (preset: Preset) => {
    setBusy({ kind: "inject", presetId: preset.id });
    try {
      if (preset.deployFirst) {
        await deploy(preset.service);
        appendLog(`Deploy marker written for ${preset.service}.`);
      }
      await injectFault(preset.service, preset.spec);
      setActiveFault({
        service: preset.service,
        spec: preset.spec,
        label: preset.label,
        startedAt: Date.now(),
      });
      appendLog(`Injected "${preset.label}" on ${preset.service}.`);
    } catch (err) {
      appendLog(`Failed to inject "${preset.label}": ${errMessage(err)}`);
    } finally {
      setBusy(null);
    }
  };

  const runCustomFault = async () => {
    const spec: FaultSpec = {
      type: customType,
      magnitude: customMagnitude,
      duration_seconds: customDuration,
    };
    setBusy({ kind: "inject" });
    try {
      if (customDeploy) {
        await deploy(customService);
        appendLog(`Deploy marker written for ${customService}.`);
      }
      await injectFault(customService, spec);
      setActiveFault({
        service: customService,
        spec,
        label: `custom: ${customType}`,
        startedAt: Date.now(),
      });
      appendLog(
        `Injected custom fault (${customType}, magnitude ${customMagnitude}) on ${customService}.`,
      );
    } catch (err) {
      appendLog(`Failed to inject custom fault: ${errMessage(err)}`);
    } finally {
      setBusy(null);
    }
  };

  const clearActive = async () => {
    if (!activeFault) return;
    setBusy({ kind: "clear" });
    try {
      await clearFault(activeFault.service);
      appendLog(`Cleared fault on ${activeFault.service}.`);
      setActiveFault(null);
    } catch (err) {
      appendLog(`Failed to clear fault on ${activeFault.service}: ${errMessage(err)}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-ink">Operations</h1>
        <p className="mt-1 text-sm text-ink-3">
          Inject controlled faults into Meridian services to exercise the monitoring pipeline.
        </p>
      </div>

      <ServiceStatusStrip activeFault={activeFault} />

      {/* Sequential-injection guard banner */}
      <div
        className={`card flex items-start gap-3 p-4 ${
          guardActive ? "border-data-warn/40 bg-amber-50" : ""
        }`}
      >
        <div
          className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${
            guardActive ? "bg-data-warn animate-pulseDot" : "bg-brand"
          }`}
        />
        <div className="text-sm">
          {guardActive ? (
            <>
              <span className="font-semibold text-ink">
                Active fault: {activeFault!.label} on {activeFault!.service}.
              </span>{" "}
              <span className="text-ink-2">
                IntelliOps groups anomalies in a ~15s window — inject one fault at a time. Clear
                this fault and wait for the window to close before injecting the next.
              </span>
            </>
          ) : (
            <span className="text-ink-2">
              No active fault. Ready to inject — presets and the custom composer are enabled.
            </span>
          )}
        </div>
        {guardActive && (
          <button
            type="button"
            onClick={clearActive}
            disabled={busy?.kind === "clear"}
            className="btn-danger ml-auto shrink-0"
          >
            {busy?.kind === "clear" ? "Clearing…" : "Clear"}
          </button>
        )}
      </div>

      {/* Presets */}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-ink">Scenario presets</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PRESETS.map((preset) => (
            <div key={preset.id} className="card flex flex-col gap-3 p-4">
              <div>
                <div className="text-sm font-semibold text-ink">{preset.label}</div>
                <div className="mt-1 text-xs text-ink-3">{preset.description}</div>
              </div>
              <div className="mt-auto flex items-center gap-2">
                <span className="pill-neutral text-2xs">{preset.service}</span>
                <span className="pill-neutral text-2xs">{preset.spec.type}</span>
              </div>
              <button
                type="button"
                className="btn-secondary w-full"
                disabled={injectDisabled}
                onClick={() => runPreset(preset)}
              >
                {busy?.kind === "inject" && busy.presetId === preset.id
                  ? "Injecting…"
                  : "Inject"}
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* Custom fault composer */}
      <section className="card p-5">
        <h2 className="mb-4 text-sm font-semibold text-ink">Custom fault composer</h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <label className="label" htmlFor="custom-service">
              Target service
            </label>
            <select
              id="custom-service"
              className="input"
              value={customService}
              onChange={(e) => setCustomService(e.target.value as MeridianService)}
              disabled={injectDisabled}
            >
              {SERVICES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="custom-type">
              Fault type
            </label>
            <select
              id="custom-type"
              className="input"
              value={customType}
              onChange={(e) => setCustomType(e.target.value as FaultType)}
              disabled={injectDisabled}
            >
              {FAULT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="custom-magnitude">
              Magnitude ({customMagnitude.toFixed(2)})
            </label>
            <input
              id="custom-magnitude"
              type="range"
              min={0.1}
              max={2}
              step={0.05}
              value={customMagnitude}
              onChange={(e) => setCustomMagnitude(Number.parseFloat(e.target.value))}
              disabled={injectDisabled}
              className="mt-2.5 w-full accent-brand"
            />
          </div>
          <div>
            <label className="label" htmlFor="custom-duration">
              Duration (seconds)
            </label>
            <input
              id="custom-duration"
              type="number"
              min={5}
              step={5}
              className="input"
              value={customDuration}
              onChange={(e) => setCustomDuration(Number.parseInt(e.target.value, 10) || 0)}
              disabled={injectDisabled}
            />
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between border-t border-line pt-4">
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input
              type="checkbox"
              checked={customDeploy}
              onChange={(e) => setCustomDeploy(e.target.checked)}
              disabled={injectDisabled}
              className="h-4 w-4 rounded border-line-strong accent-brand"
            />
            Mark as deploy (writes a deploy marker before the fault)
          </label>
          <button
            type="button"
            className="btn-primary"
            disabled={injectDisabled}
            onClick={runCustomFault}
          >
            {busy?.kind === "inject" && !busy.presetId ? "Injecting…" : "Inject custom fault"}
          </button>
        </div>
      </section>

      {/* Activity log */}
      <section className="card p-5">
        <h2 className="mb-3 text-sm font-semibold text-ink">Activity</h2>
        {log.length === 0 ? (
          <p className="text-sm text-ink-4">No operations yet this session.</p>
        ) : (
          <ul className="space-y-1.5 font-mono text-xs text-ink-2">
            {log.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ServiceStatusStrip({ activeFault }: { activeFault: ActiveFault | null }) {
  return (
    <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {SERVICES.map((service) => {
        const isFaulted = activeFault?.service === service;
        return (
          <div key={service} className="card flex items-center justify-between p-4">
            <div>
              <div className="text-sm font-semibold capitalize text-ink">{service}</div>
              <div className="mt-1 text-2xs text-ink-4">meridian-{service}</div>
            </div>
            <StatusPill tone={isFaulted ? "crit" : "ok"} pulse={isFaulted}>
              {isFaulted ? "Degraded" : "Healthy"}
            </StatusPill>
          </div>
        );
      })}
    </section>
  );
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : "unknown error";
}
