import { useEffect, useState } from "react";
import { loadReports } from "../data/api";
import {
  seededAggregateFigures,
  seededPeriods,
  seededRecentReports,
} from "../data/mock";
import StatusPill from "../components/StatusPill";
import type { TrafficTick } from "../data/useBackgroundTraffic";

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const percent = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

function periodTone(status: string): "ok" | "warn" | "neutral" {
  if (status === "Locked") return "neutral";
  if (status === "Closing") return "warn";
  return "ok";
}

function reportTone(status: string): "ok" | "warn" | "neutral" {
  if (status === "Final") return "ok";
  if (status === "Under Review") return "warn";
  return "neutral";
}

export default function Dashboard({ lastTick }: { lastTick: TrafficTick | null }) {
  const [liveReportCount, setLiveReportCount] = useState<number | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadReports()
      .then((r) => {
        if (!cancelled) setLiveReportCount(r.reports.length);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [lastTick]);

  const figures = seededAggregateFigures;

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-ink">Dashboard</h1>
          <p className="mt-1 text-sm text-ink-3">
            Reporting overview across all active clients and periods.
          </p>
        </div>
        <div className="text-right">
          <div className="text-2xs uppercase tracking-wide text-ink-4">Live reports (gateway)</div>
          <div className="mt-0.5 font-serif text-xl font-semibold text-ink">
            {loadError ? "—" : (liveReportCount ?? "…")}
          </div>
        </div>
      </div>

      {/* Aggregate figures */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Total submitted YTD"
          value={currency.format(figures.totalSubmittedYtd)}
        />
        <StatCard label="Reports generated YTD" value={String(figures.totalReportsYtd)} />
        <StatCard
          label="Avg. processing time"
          value={`${figures.avgProcessingHours.toFixed(1)} hrs`}
        />
        <StatCard
          label="Reconciliation rate"
          value={percent.format(figures.reconciliationRate)}
          tone="ok"
        />
      </div>

      {/* Reporting periods */}
      <section className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">Reporting periods</h2>
          <span className="text-2xs uppercase tracking-wide text-ink-4">
            {seededPeriods.length} periods tracked
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-line text-2xs uppercase tracking-wide text-ink-4">
                <th className="pb-2 font-semibold">Period</th>
                <th className="pb-2 font-semibold">Status</th>
                <th className="pb-2 font-semibold">Submissions</th>
                <th className="pb-2 font-semibold">Due date</th>
              </tr>
            </thead>
            <tbody>
              {seededPeriods.map((p) => (
                <tr key={p.period} className="border-b border-line last:border-0">
                  <td className="py-3 font-medium text-ink">{p.label}</td>
                  <td className="py-3">
                    <StatusPill tone={periodTone(p.status)}>{p.status}</StatusPill>
                  </td>
                  <td className="py-3 text-ink-2">
                    {p.submissionsReceived}
                    <span className="text-ink-4"> / {p.submissionsExpected}</span>
                    <div className="mt-1 h-1.5 w-32 overflow-hidden rounded-full bg-surface-sunken">
                      <div
                        className="h-full rounded-full bg-brand"
                        style={{
                          width: `${Math.min(
                            100,
                            (p.submissionsReceived / p.submissionsExpected) * 100,
                          )}%`,
                        }}
                      />
                    </div>
                  </td>
                  <td className="py-3 text-ink-2">{p.dueDate}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Recent reports */}
      <section className="card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-ink">Recent reports</h2>
        </div>
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
              {seededRecentReports.map((r) => (
                <tr key={r.id} className="border-b border-line last:border-0">
                  <td className="py-3 font-mono text-xs text-ink-3">{r.id}</td>
                  <td className="py-3 font-medium text-ink">{r.client}</td>
                  <td className="py-3 text-ink-2">{r.period}</td>
                  <td className="py-3 text-ink-2">{r.summary}</td>
                  <td className="py-3">
                    <StatusPill tone={reportTone(r.status)}>{r.status}</StatusPill>
                  </td>
                  <td className="py-3 text-ink-3">
                    {new Date(r.generatedAt).toLocaleDateString(undefined, {
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

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "ok";
}) {
  return (
    <div className="card p-5">
      <div className="text-2xs font-semibold uppercase tracking-wide text-ink-4">{label}</div>
      <div
        className={`mt-2 font-serif text-2xl font-semibold ${
          tone === "ok" ? "text-brand-dim" : "text-ink"
        }`}
      >
        {value}
      </div>
    </div>
  );
}
