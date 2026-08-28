import { useEffect, useState } from "react";
import { loadMetrics, type MetricsResult } from "../data/api";
import StatusPill from "../components/StatusPill";

export default function Metrics() {
  const [data, setData] = useState<MetricsResult>({ scraped: false, services: [] });

  useEffect(() => {
    let alive = true;
    const tick = () => loadMetrics().then((d) => alive && setData(d)).catch(() => {});
    tick();
    const id = window.setInterval(tick, 3000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-ink">Live telemetry</h1>
        <p className="mt-1 text-sm text-ink-3">
          Real Prometheus metrics for each Meridian service, polled every 3s. Break a service in
          Operations and watch its CPU climb here.
        </p>
      </div>

      {!data.scraped || data.services.length === 0 ? (
        <div className="card p-8 text-center text-sm text-ink-3">
          No metrics — is Prometheus running? (compose brings it up on :9090)
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {data.services.map((s) => {
            const cpu = s.cpu_usage;
            const hot = cpu != null && cpu >= 50;
            return (
              <div key={s.service} className="card p-5">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-ink">{s.service}</span>
                  <StatusPill tone={s.healthy ? "ok" : "crit"} pulse={!s.healthy}>
                    {s.healthy ? "healthy" : "degraded"}
                  </StatusPill>
                </div>
                <div className="mt-4 flex items-end justify-between">
                  <div>
                    <div className="text-2xs uppercase tracking-wide text-ink-4">CPU usage</div>
                    <div
                      className={`text-3xl font-semibold tabular-nums ${
                        hot ? "text-data-neg" : "text-ink"
                      }`}
                    >
                      {cpu != null ? cpu.toFixed(0) : "—"}
                      {cpu != null && <span className="text-base text-ink-3">%</span>}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-2xs uppercase tracking-wide text-ink-4">Error rate</div>
                    <div className="text-lg font-medium tabular-nums text-ink-2">
                      {s.error_rate != null ? `${(s.error_rate * 100).toFixed(0)}%` : "—"}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
