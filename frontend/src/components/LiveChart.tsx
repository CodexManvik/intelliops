import { useMemo, useState } from "react";
import type { MetricHistory } from "../data/types";

/* ---------------------------------------------------------------------------
   A real metric chart.

   What it replaces: four "sparklines" fed by a seeded PRNG from the mock module
   and memoised with [], so they rendered an identical fabricated curve in live
   mode for the whole session. This one draws GET /metrics/history, which is
   Prometheus proxied by read-service.

   Deliberate choices:
   - When Prometheus is unreachable it says so. A metrics chart that invents a
     shape when it has no data is worse than no chart.
   - Axes and a hover readout, so a value and a time window can actually be
     read. The old sparkline was aria-hidden with no scale at all.
   - Transitions are CSS on `d`/opacity only, and are dropped entirely under
     prefers-reduced-motion (see index.css).
--------------------------------------------------------------------------- */

const PALETTE = ["#0A84FF", "#34C759", "#FF9500", "#AF52DE", "#FF3B30", "#5E5CE6"];

function niceTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function fmt(v: number): string {
  if (Math.abs(v) >= 1000) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

export function LiveChart({
  history,
  height = 180,
  unit = "",
  maxSeries = 6,
}: {
  history: MetricHistory;
  height?: number;
  unit?: string;
  maxSeries?: number;
}) {
  const [hoverX, setHoverX] = useState<number | null>(null);

  const model = useMemo(() => {
    const series = history.series.filter((s) => s.points.length > 0).slice(0, maxSeries);
    if (series.length === 0) return null;
    const all = series.flatMap((s) => s.points);
    const tMin = Math.min(...all.map((p) => p[0]));
    const tMax = Math.max(...all.map((p) => p[0]));
    const vMinRaw = Math.min(...all.map((p) => p[1]));
    const vMaxRaw = Math.max(...all.map((p) => p[1]));
    // Pad so a flat line sits mid-chart instead of hugging an edge.
    const pad = (vMaxRaw - vMinRaw) * 0.15 || Math.max(Math.abs(vMaxRaw) * 0.1, 1);
    return { series, tMin, tMax, vMin: vMinRaw - pad, vMax: vMaxRaw + pad, vMinRaw, vMaxRaw };
  }, [history, maxSeries]);

  if (!history.available || !model) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-black/[0.08] bg-black/[0.015]"
        style={{ height }}
      >
        <span className="font-mono text-2xs uppercase tracking-[0.14em] text-ink-3">no data</span>
        <span className="font-mono text-2xs text-ink-3">
          {history.reason ?? `no samples for ${history.metric}`}
        </span>
      </div>
    );
  }

  const W = 1000; // viewBox units; the SVG scales to its container
  const H = height;
  const padL = 44;
  const padR = 12;
  const padT = 10;
  const padB = 22;
  const iw = W - padL - padR;
  const ih = H - padT - padB;

  const x = (t: number) =>
    padL + (model.tMax === model.tMin ? iw : ((t - model.tMin) / (model.tMax - model.tMin)) * iw);
  const y = (v: number) =>
    padT + (model.vMax === model.vMin ? ih / 2 : (1 - (v - model.vMin) / (model.vMax - model.vMin)) * ih);

  const gridVals = [model.vMaxRaw, (model.vMaxRaw + model.vMinRaw) / 2, model.vMinRaw];

  // Nearest sample to the cursor, for the readout.
  const hoverT =
    hoverX === null ? null : model.tMin + ((hoverX - padL) / iw) * (model.tMax - model.tMin);

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`${history.metric} over the last ${Math.round((model.tMax - model.tMin) / 60)} minutes`}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHoverX(((e.clientX - r.left) / r.width) * W);
        }}
        onMouseLeave={() => setHoverX(null)}
      >
        {gridVals.map((v, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={W - padR}
              y1={y(v)}
              y2={y(v)}
              stroke="currentColor"
              className="text-ink-3"
              strokeOpacity="0.14"
              strokeDasharray={i === 1 ? "3 4" : undefined}
            />
            <text
              x={padL - 6}
              y={y(v) + 3}
              textAnchor="end"
              className="fill-ink-3 font-mono"
              style={{ fontSize: 9 }}
            >
              {fmt(v)}
            </text>
          </g>
        ))}

        <text x={padL} y={H - 6} className="fill-ink-3 font-mono" style={{ fontSize: 9 }}>
          {niceTime(model.tMin)}
        </text>
        <text
          x={W - padR}
          y={H - 6}
          textAnchor="end"
          className="fill-ink-3 font-mono"
          style={{ fontSize: 9 }}
        >
          {niceTime(model.tMax)}
        </text>

        {model.series.map((s, si) => {
          const color = PALETTE[si % PALETTE.length];
          const d = s.points
            .map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`)
            .join(" ");
          const last = s.points[s.points.length - 1];
          return (
            <g key={s.service}>
              <path
                d={d}
                fill="none"
                stroke={color}
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="chart-line"
              />
              <circle cx={x(last[0])} cy={y(last[1])} r="3.5" fill={color} />
              <circle cx={x(last[0])} cy={y(last[1])} r="7" fill={color} fillOpacity="0.2">
                <animate
                  attributeName="r"
                  values="5;9;5"
                  dur="2.4s"
                  repeatCount="indefinite"
                  className="chart-pulse"
                />
              </circle>
            </g>
          );
        })}

        {hoverX !== null && hoverX >= padL && hoverX <= W - padR && (
          <line
            x1={hoverX}
            x2={hoverX}
            y1={padT}
            y2={padT + ih}
            stroke="currentColor"
            className="text-ink-3"
            strokeOpacity="0.35"
          />
        )}
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
        {model.series.map((s, si) => {
          const color = PALETTE[si % PALETTE.length];
          let shown = s.points[s.points.length - 1][1];
          if (hoverT !== null) {
            let best = s.points[0];
            for (const p of s.points) {
              if (Math.abs(p[0] - hoverT) < Math.abs(best[0] - hoverT)) best = p;
            }
            shown = best[1];
          }
          return (
            <span key={s.service} className="flex items-center gap-1.5 font-mono text-2xs">
              <span className="h-2 w-2 flex-none rounded-full" style={{ background: color }} />
              <span className="text-ink-3">{s.service}</span>
              <span className="tabular-nums text-ink">
                {fmt(shown)}
                {unit}
              </span>
            </span>
          );
        })}
        {hoverT !== null && (
          <span className="ml-auto font-mono text-2xs text-ink-3">at {niceTime(hoverT)}</span>
        )}
      </div>
    </div>
  );
}
