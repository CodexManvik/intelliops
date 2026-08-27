import { useState } from "react";
import {
  CheckCircle,
  Circuitry,
  Gauge,
  Key,
  Plugs,
  Warning,
  WarningCircle,
} from "@phosphor-icons/react";
import { Bezel, Eyebrow } from "../components/primitives";
import { loadBaseline, loadLlmConfig, loadSystem, setLlmConfig, testLlmConfig } from "../data/source";
import { system as mockSystem, baseline as mockBaseline } from "../data/mock";
import { useLiveData } from "../hooks/useLiveData";
import { Reveal as Section } from "../hooks/useReveal";
import { pushToast } from "../hooks/useToast";
import type { LlmProbe, SystemInfo } from "../data/types";

/** Provider badge is derived strictly from the live `/config/llm` response —
 * never from what the operator just typed into the form. `provider` and
 * `last_probe` are both server-reported, so this can't drift into a
 * fabricated "connected" state. */
function llmBadge(llm: SystemInfo["llm"]) {
  if (llm.provider === "openai-compatible" && llm.last_probe?.ok) {
    return {
      tone: "text-sev-ok bg-sev-ok/10 border-sev-ok/25",
      icon: <CheckCircle size={12} weight="fill" />,
      label: `LLM: connected (${llm.model})`,
    };
  }
  if (llm.endpoint_configured && llm.last_probe?.ok === false) {
    return {
      tone: "text-sev-warn bg-sev-warn/10 border-sev-warn/25",
      icon: <Warning size={12} weight="fill" />,
      label: "LLM error → template fallback",
    };
  }
  return {
    tone: "text-ink-2 bg-black/[0.05] border-black/[0.10]",
    icon: <Circuitry size={12} weight="light" />,
    label: "Template (no endpoint set)",
  };
}

function StateRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg bg-black/[0.03] px-3 py-2.5">
      <span className="w-32 text-2xs font-medium uppercase tracking-[0.1em] text-ink-3">{label}</span>
      <span className="font-mono text-2xs text-ink">{value}</span>
    </div>
  );
}

export function System() {
  const { data: sys } = useLiveData(loadSystem, mockSystem);
  const { data: baseline } = useLiveData(loadBaseline, mockBaseline);
  const { data: llm } = useLiveData(loadLlmConfig, mockSystem.llm);

  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-4o-mini");
  const [probe, setProbe] = useState<LlmProbe | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);

  const badge = llmBadge(llm);

  const cfg = () => ({ endpoint, api_key: apiKey, model });

  const handleTest = async () => {
    setTesting(true);
    setProbe(null);
    try {
      const result = await testLlmConfig(cfg());
      setProbe(result);
      pushToast(result.ok ? "success" : "error", result.ok ? "Connection OK" : result.error ?? "Test failed");
    } catch (e) {
      const msg = String(e);
      setProbe({ ok: false, error: msg });
      pushToast("error", msg);
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await setLlmConfig(cfg());
      pushToast("success", "LLM config saved");
      // api_key is intentionally not cleared from local state here — the user
      // may want to Test again — but it is never rendered anywhere.
    } catch (e) {
      pushToast("error", String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <Section>
        <Eyebrow>
          <Circuitry size={12} weight="light" /> Under the hood
        </Eyebrow>
        <h1 className="mt-4 text-4xl font-semibold tracking-tightest sm:text-5xl">
          What&apos;s actually <span className="text-signal">running.</span>
        </h1>
        <p className="mt-3 max-w-[58ch] text-base leading-relaxed text-ink-2">
          No staged demo state — this reads the live correlator, its learned baselines, and the
          configured backends directly from the running services.
        </p>
      </Section>

      {/* (1) system-state grid */}
      <Section>
        <Bezel coreClassName="p-6">
          <div className="mb-4 flex items-center gap-2">
            <Gauge size={16} weight="light" className="text-ink-2" />
            <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">System state · GET /system</span>
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
            <StateRow label="Correlator" value={sys.correlator_kind} />
            <StateRow label="Bus backend" value={sys.bus_backend} />
            <StateRow label="Store backend" value={sys.store_backend} />
            <StateRow label="Remediation" value={sys.remediator_mode} />
            <StateRow label="Auth mode" value={sys.auth_mode} />
            <StateRow label="LLM provider" value={sys.llm.provider} />
          </div>
        </Bezel>
      </Section>

      {/* (2) live baselines */}
      <Section>
        <Bezel coreClassName="p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Gauge size={16} weight="light" className="text-ink-2" />
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Live z-score baselines · {baseline.correlator_kind} · GET /baseline
              </span>
            </div>
            <span className="font-mono text-2xs text-ink-3">{baseline.baselines.length} metrics</span>
          </div>

          {baseline.baselines.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse font-mono text-2xs">
                <thead>
                  <tr className="text-left text-ink-3">
                    <th className="pb-2 pr-4 font-medium uppercase tracking-[0.1em]">Metric</th>
                    <th className="pb-2 pr-4 font-medium uppercase tracking-[0.1em]">Mean</th>
                    <th className="pb-2 pr-4 font-medium uppercase tracking-[0.1em]">Std dev</th>
                    <th className="pb-2 font-medium uppercase tracking-[0.1em]">Samples</th>
                  </tr>
                </thead>
                <tbody>
                  {baseline.baselines.map((b, i) => (
                    <tr key={i} className="border-t border-black/[0.06]">
                      <td className="py-2 pr-4 text-ink">{b.metric_name}</td>
                      <td className="py-2 pr-4 text-ink-2 tnum">{b.mean.toFixed(3)}</td>
                      <td className="py-2 pr-4 text-ink-2 tnum">{b.std.toFixed(3)}</td>
                      <td className="py-2 text-ink-3 tnum">{b.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="rounded-2xl border border-black/[0.06] p-8 text-center text-ink-3">
              No baselines learned yet — the correlator populates this as metrics stream in.
            </div>
          )}
        </Bezel>
      </Section>

      {/* (3) LLM settings */}
      <Section>
        <Bezel coreClassName="p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Plugs size={16} weight="light" className="text-ink-2" />
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                LLM settings · GET/POST /config/llm
              </span>
            </div>
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-2xs ${badge.tone}`}>
              {badge.icon}
              {badge.label}
            </span>
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <label className="block">
              <span className="mb-1.5 block text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Endpoint
              </span>
              <input
                type="text"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="https://api.openai.com/v1"
                className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 font-mono text-2xs text-ink placeholder:text-ink-4 focus:border-signal/40 focus:outline-none focus:ring-2 focus:ring-signal/15"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 flex items-center gap-1 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                <Key size={11} weight="light" /> API key
              </span>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="sk-…"
                autoComplete="off"
                className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 font-mono text-2xs text-ink placeholder:text-ink-4 focus:border-signal/40 focus:outline-none focus:ring-2 focus:ring-signal/15"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 block text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Model
              </span>
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="gpt-4o-mini"
                className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 font-mono text-2xs text-ink placeholder:text-ink-4 focus:border-signal/40 focus:outline-none focus:ring-2 focus:ring-signal/15"
              />
            </label>
          </div>

          <p className="mt-2 font-mono text-2xs text-ink-3">
            The key is held only in this form to send with Test/Save — it is never echoed back or displayed.
            Current configured endpoint: <span className="text-ink-2">{llm.endpoint || "(none)"}</span>
          </p>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !endpoint}
              className="rounded-full border border-black/[0.10] bg-black/[0.04] px-5 py-2 text-sm font-medium text-ink transition-colors duration-300 hover:bg-black/[0.06] disabled:opacity-40"
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-full bg-signal px-5 py-2 text-sm font-medium text-white shadow-[0_8px_24px_-6px_rgba(0,113,227,0.35)] transition-all duration-300 hover:shadow-[0_12px_32px_-6px_rgba(0,113,227,0.45)] disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save"}
            </button>

            {probe && (
              <span className={`flex items-center gap-1.5 font-mono text-2xs ${probe.ok ? "text-sev-ok" : "text-sev-crit"}`}>
                {probe.ok ? <CheckCircle size={13} weight="fill" /> : <WarningCircle size={13} weight="fill" />}
                {probe.ok
                  ? `ok · ${probe.model ?? model} · ${probe.latency_ms}ms`
                  : probe.error ?? "failed"}
              </span>
            )}
          </div>
        </Bezel>
      </Section>
    </div>
  );
}
