import { useCallback, useEffect, useState } from "react";
import { loadReports } from "../data/api";
import { seededRecentReports, type RecentReport } from "../data/mock";
import StatusPill from "../components/StatusPill";
import type { TrafficTick } from "../data/useBackgroundTraffic";

function reportTone(status: string): "ok" | "warn" | "neutral" {
  if (status === "Final") return "ok";
  if (status === "Under Review") return "warn";
  return "neutral";
}

type LoadState = "idle" | "loading" | "loaded" | "error";

export default function Reports({ lastTick }: { lastTick: TrafficTick | null }) {
  const [liveCount, setLiveCount] = useState<number | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [generated, setGenerated] = useState<RecentReport[]>([]);

  const refresh = useCallback(() => {
    setLoadState("loading");
    loadReports()
      .then((r) => {
        setLiveCount(r.reports.length);
        setLoadState("loaded");
      })
      .catch(() => setLoadState("error"));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh, lastTick]);

  const handleGenerate = () => {
    const now = new Date();
    const entry: RecentReport = {
      id: `RPT-${Math.floor(10000 + Math.random() * 89999)}`,
      client: "Northwind Industrial",
      period: "FY26-Q2",
      summary: "Ad-hoc reconciliation snapshot",
      status: "Draft",
      generatedAt: now.toISOString(),
    };
    setGenerated((prev) => [entry, ...prev]);
    refresh();
  };

  const allReports = [...generated, ...seededRecentReports];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-ink">Reports</h1>
          <p className="mt-1 text-sm text-ink-3">
            Generated financial reports across all clients and periods.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={handleGenerate}>
          Generate report
        </button>
      </div>

      <div className="flex items-center gap-3 text-sm text-ink-3">
        <span>
          Gateway report count:{" "}
          <span className="font-semibold text-ink">
            {loadState === "error" ? "unavailable" : (liveCount ?? "…")}
          </span>
        </span>
        <button
          type="button"
          onClick={refresh}
          className="text-2xs font-semibold uppercase tracking-wide text-brand-dim hover:underline"
        >
          Refresh
        </button>
      </div>

      <section className="card p-5">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-line text-2xs uppercase tracking-wide text-ink-4">
                <th className="pb-2 font-semibold">ID</th>
                <th className="pb-2 font-semibold">Client</th>
                <th className="pb-2 font-semibold">Period</th>
                <th className="pb-2 font-semibold">Summary</th>
                <th className="pb-2 font-semibold">Status</th>
                <th className="pb-2 font-semibold">Generated</th>
              </tr>
            </thead>
            <tbody>
              {allReports.map((r) => (
                <tr key={r.id} className="border-b border-line last:border-0">
                  <td className="py-3 font-mono text-xs text-ink-3">{r.id}</td>
                  <td className="py-3 font-medium text-ink">{r.client}</td>
                  <td className="py-3 text-ink-2">{r.period}</td>
                  <td className="py-3 text-ink-2">{r.summary}</td>
                  <td className="py-3">
                    <StatusPill tone={reportTone(r.status)}>{r.status}</StatusPill>
                  </td>
                  <td className="py-3 text-ink-3">
                    {new Date(r.generatedAt).toLocaleString(undefined, {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
