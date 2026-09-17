import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
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
   - Gridlines land on round numbers, not on the data's own min/mid/max. The
     previous version labelled whatever the extremes happened to be, which put
     axis labels like 17.43 on the page and made two charts impossible to
     compare at a glance.
   - One crosshair reads every series at once. Hovering to get a single line's
     value is useless when the question is "did these move together".
   - `compact` drops the axes and legend for the small tiles, where a 104px box
     cannot carry them without stacking them on top of each other.
   - The viewBox is sized to the measured container rather than to a fixed 1000
     units. A fixed viewBox forces a choice between `preserveAspectRatio="none"`
     (which stretches the axis text and turns the endpoint dots into ellipses --
     badly so in a 230px tile, where the horizontal scale is 0.23) and the
     default `meet` (which shrinks the whole plot to a sliver in the middle).
     Measuring removes the choice: everything draws 1:1.
   - Transitions are CSS on `d`/opacity only, and are dropped entirely under
     prefers-reduced-motion (see index.css).
--------------------------------------------------------------------------- */

// Series colours resolve through CSS custom properties so they re-pick
// themselves per theme: the dark set is tuned for a near-black panel, and the
// light set is darker because #62C073 on white is 2.1:1 and a 1.6px stroke in
// it simply vanishes. Applied via `style` rather than as an SVG presentation
// attribute, so the value is parsed as CSS and var() resolves.
const PALETTE = [
  "rgb(var(--chart-1))",
  "rgb(var(--chart-2))",
  "rgb(var(--chart-3))",
  "rgb(var(--chart-4))",
  "rgb(var(--chart-5))",
  "rgb(var(--chart-6))",
];

function niceTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function fmt(v: number): string {
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  if (a === 0) return "0";
  return v.toPrecision(2);
}

/** Round gridline values: the 1 / 2 / 5 x 10^n ladder, so an axis reads 20, 25,
 *  30 rather than 17.43, 19.02, 20.61. */
function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) out.push(t);
  return out;
}

export function LiveChart({
  history,
  height = 180,
  unit = "",
  maxSeries = 6,
  compact = false,
}: {
  history: MetricHistory;
  height?: number;
  unit?: string;
  maxSeries?: number;
  compact?: boolean;
}) {
  const [hoverX, setHoverX] = useState<number | null>(null);
  // Per-instance, not per-metric: the Overview draws cpu_usage in a tile and
  // again in the large chart, and two <linearGradient> elements sharing an id
  // is invalid SVG. Declared up here with the other hooks because the no-data
  // branch below returns early.
  const gid = useId().replace(/[^a-zA-Z0-9]/g, "");
  // A callback ref, not useRef + useEffect([]): the first render of this
  // component is usually the "no data yet" branch, which mounts a different
  // element. A ref object would still be null when an empty-deps effect ran,
  // and nothing would ever re-run it, so the chart stayed at its 600px
  // fallback width and sat centred in a wider card.
  const [boxW, setBoxW] = useState(0);
  const observed = useRef<ResizeObserver | null>(null);
  const measure = useCallback((el: HTMLDivElement | null) => {
    observed.current?.disconnect();
    observed.current = null;
    if (!el) return;
    setBoxW(el.clientWidth);
    const ro = new ResizeObserver(([e]) => setBoxW(e.contentRect.width));
    ro.observe(el);
    observed.current = ro;
  }, []);
  useEffect(() => () => observed.current?.disconnect(), []);

  const model = useMemo(() => {
    const series = history.series.filter((s) => s.points.length > 0).slice(0, maxSeries);
    if (series.length === 0) return null;
    const all = series.flatMap((s) => s.points);
    const tMin = Math.min(...all.map((p) => p[0]));
    const tMax = Math.max(...all.map((p) => p[0]));
    const vMinRaw = Math.min(...all.map((p) => p[1]));
    const vMaxRaw = Math.max(...all.map((p) => p[1]));
    // Headroom so the newest point never touches an edge, and so a genuinely
    // flat series sits mid-box instead of hugging the floor.
    const pad = (vMaxRaw - vMinRaw) * 0.18 || Math.max(Math.abs(vMaxRaw) * 0.12, 1);
    return { series, tMin, tMax, vMin: vMinRaw - pad, vMax: vMaxRaw + pad };
  }, [history, maxSeries]);

  if (!history.available || !model) {
    return (
      <div
        ref={measure}
        className="flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-line-strong"
        style={{ height }}
      >
        <span className="font-mono text-2xs uppercase tracking-[0.14em] text-ink-3">no data</span>
        {!compact && (
          <span className="font-mono text-2xs text-ink-4">
            {history.reason ?? `no samples for ${history.metric}`}
          </span>
        )}
      </div>
    );
  }

  const W = Math.max(boxW || 600, 160); // viewBox units == CSS pixels
  const H = height;
  const padL = compact ? 4 : 46;
  const padR = compact ? 4 : 10;
  const padT = compact ? 6 : 10;
  const padB = compact ? 6 : 20;
  const iw = W - padL - padR;
  const ih = H - padT - padB;

  const x = (t: number) =>
    padL + (model.tMax === model.tMin ? iw : ((t - model.tMin) / (model.tMax - model.tMin)) * iw);
  const y = (v: number) =>
    padT +
    (model.vMax === model.vMin ? ih / 2 : (1 - (v - model.vMin) / (model.vMax - model.vMin)) * ih);

  const ticks = compact ? [] : niceTicks(model.vMin, model.vMax);
  const timeTicks = compact
    ? []
    : [0, 0.5, 1].map((f) => model.tMin + f * (model.tMax - model.tMin));

  // Nearest sample to the cursor, for the readout.
  const inPlot = hoverX !== null && hoverX >= padL && hoverX <= W - padR;
  const hoverT = inPlot ? model.tMin + ((hoverX! - padL) / iw) * (model.tMax - model.tMin) : null;
  const readout =
    hoverT === null
      ? null
      : model.series.map((s, si) => {
          let best = s.points[0];
          for (const p of s.points) {
            if (Math.abs(p[0] - hoverT) < Math.abs(best[0] - hoverT)) best = p;
          }
          return {
            service: s.service,
            value: best[1],
            color: PALETTE[si % PALETTE.length],
            t: best[0],
          };
        });

  // Fills stack, so five overlapping areas at the single-series opacity would
  // silt up into one grey wash. Scale the fill down as series are added.
  const fillTop = model.series.length === 1 ? 0.22 : 0.09;

  return (
    <div className="relative" ref={measure}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height }}
        role="img"
        aria-label={`${history.metric} over the last ${Math.round(
          (model.tMax - model.tMin) / 60,
        )} minutes`}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHoverX(((e.clientX - r.left) / r.width) * W);
        }}
        onMouseLeave={() => setHoverX(null)}
      >
        <defs>
          {model.series.map((s, si) => (
            <linearGradient key={s.service} id={`fill-${gid}-${si}`} x1="0" x2="0" y1="0" y2="1">
              <stop
                offset="0%"
                stopOpacity={fillTop}
                style={{ stopColor: PALETTE[si % PALETTE.length] }}
              />
              <stop offset="100%" stopOpacity="0" style={{ stopColor: PALETTE[si % PALETTE.length] }} />
            </linearGradient>
          ))}
        </defs>

        {ticks.map((v, i) => (
          <g key={i}>
            <line
              x1={padL}
              x2={W - padR}
              y1={y(v)}
              y2={y(v)}
              strokeWidth="1"
              style={{ stroke: "rgb(var(--line))" }}
            />
            <text
              x={padL - 8}
              y={y(v) + 3}
              textAnchor="end"
              className="fill-ink-4 font-mono"
              style={{ fontSize: 9 }}
            >
              {fmt(v)}
            </text>
          </g>
        ))}

        {timeTicks.map((t, i) => (
          <text
            key={i}
            x={i === 0 ? padL : i === timeTicks.length - 1 ? W - padR : padL + iw / 2}
            y={H - 5}
            textAnchor={i === 0 ? "start" : i === timeTicks.length - 1 ? "end" : "middle"}
            className="fill-ink-4 font-mono"
            style={{ fontSize: 9 }}
          >
            {niceTime(t)}
          </text>
        ))}

        {model.series.map((s, si) => {
          const color = PALETTE[si % PALETTE.length];
          const d = s.points
            .map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`)
            .join(" ");
          const first = s.points[0];
          const last = s.points[s.points.length - 1];
          const floor = (padT + ih).toFixed(1);
          const area = `${d} L${x(last[0]).toFixed(1)},${floor} L${x(first[0]).toFixed(1)},${floor} Z`;
          return (
            <g key={s.service}>
              <path d={area} fill={`url(#fill-${gid}-${si})`} />
              <path
                d={d}
                fill="none"
                style={{ stroke: color }}
                strokeWidth={compact ? 1.4 : 1.6}
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
                className="chart-line"
              />
              <circle cx={x(last[0])} cy={y(last[1])} r="2.5" style={{ fill: color }} />
            </g>
          );
        })}

        {inPlot && (
          <line
            x1={hoverX!}
            x2={hoverX!}
            y1={padT}
            y2={padT + ih}
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
            style={{ stroke: "rgb(var(--ink-4))" }}
          />
        )}
        {inPlot &&
          readout?.map((r) => (
            <circle
              key={r.service}
              cx={x(r.t)}
              cy={y(r.value)}
              r="3"
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
              style={{ fill: "rgb(var(--ground-raised))", stroke: r.color }}
            />
          ))}
      </svg>

      {/* Crosshair readout. Positioned as HTML rather than SVG text so it can
          use the app's type stack and stay legible over any series. */}
      {inPlot && readout && (
        <div
          className="pointer-events-none absolute top-1 z-10 min-w-[150px] rounded-lg border border-line-strong bg-ground/95 p-2 backdrop-blur-sm"
          style={{
            left: `${(hoverX! / W) * 100}%`,
            transform: hoverX! > W * 0.6 ? "translateX(calc(-100% - 10px))" : "translateX(10px)",
          }}
        >
          <div className="mb-1 font-mono text-2xs text-ink-4">{niceTime(hoverT!)}</div>
          {readout.map((r) => (
            <div key={r.service} className="flex items-center gap-2 font-mono text-2xs leading-5">
              <span className="h-1.5 w-1.5 flex-none rounded-full" style={{ background: r.color }} />
              <span className="truncate text-ink-3">{r.service}</span>
              <span className="ml-auto tabular-nums text-ink">
                {fmt(r.value)}
                {unit}
              </span>
            </div>
          ))}
        </div>
      )}

      {!compact && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-2.5">
          {model.series.map((s, si) => (
            <span key={s.service} className="flex items-center gap-1.5 font-mono text-2xs">
              <span
                className="h-1.5 w-1.5 flex-none rounded-full"
                style={{ background: PALETTE[si % PALETTE.length] }}
              />
              <span className="text-ink-3">{s.service}</span>
              <span className="tabular-nums text-ink-2">
                {fmt(s.points[s.points.length - 1][1])}
                {unit}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
