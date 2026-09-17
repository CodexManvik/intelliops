import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { ArrowUpRight } from "@phosphor-icons/react";
import type { Severity, SituationStatus } from "../data/types";

/* ---------------------------------------------------------------------------
   Motion presets — spring physics, never linear/ease-in-out
--------------------------------------------------------------------------- */
export const springSoft = { type: "spring" as const, stiffness: 260, damping: 30, mass: 0.9 };
export const fluid = [0.32, 0.72, 0, 1] as const;

/* ---------------------------------------------------------------------------
   Card. Was a double bezel: an outer machined shell with its own border and
   padding wrapped around an inner core, which is an Apple hardware idiom and
   on a dark ground just reads as two muddy edges. One surface, one visible
   1px rule. The two-element structure is kept because callers pass layout on
   `className` and padding/overflow on `coreClassName`.
--------------------------------------------------------------------------- */
export function Bezel({
  children,
  className = "",
  coreClassName = "",
  glow = false,
}: {
  children: ReactNode;
  className?: string;
  coreClassName?: string;
  glow?: boolean;
}) {
  return (
    <div className={className}>
      <div
        className={`rounded-xl border bg-ground-raised shadow-lift ${
          glow ? "border-signal/45 shadow-glow" : "border-line-strong"
        } ${coreClassName}`}
      >
        {children}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Page header.

   Every view but Overview opened on a 48px marketing headline ("Situations, not
   alerts.", "Watch the agent think.") over a two-line paragraph. That is a
   landing page, and it pushed the actual working surface below the fold on a
   laptop. A console page says what it is and what you are looking at, in a
   line, and then shows it.
--------------------------------------------------------------------------- */
export function PageHead({
  title,
  hint,
  right,
}: {
  title: string;
  hint: string;
  right?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-b border-line pb-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        <p className="mt-0.5 max-w-[68ch] text-sm text-ink-3">{hint}</p>
      </div>
      {right}
    </header>
  );
}

/* ---------------------------------------------------------------------------
   Eyebrow tag — microscopic pill preceding headings
--------------------------------------------------------------------------- */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-signal/20 bg-signal/[0.10] px-3 py-1 text-2xs font-medium uppercase tracking-[0.2em] text-signal">
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------------------
   Action button. The primary variant is white-on-black rather than a coloured
   fill: on a near-black page that is the highest-contrast thing available, so
   the one control that commits a change reads louder than any accent could,
   and the blue stays reserved for data.
--------------------------------------------------------------------------- */
export function CTA({
  children,
  onClick,
  variant = "primary",
  icon = true,
  disabled = false,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost";
  icon?: boolean;
  disabled?: boolean;
}) {
  const base =
    "group inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-[13px] font-medium transition-colors duration-200 active:translate-y-px disabled:opacity-40 disabled:active:translate-y-0";
  const skin =
    variant === "primary"
      ? "bg-ink text-ground hover:bg-white"
      : "border border-line-strong text-ink-2 hover:border-ink-4 hover:text-ink";
  return (
    <button className={`${base} ${skin}`} onClick={onClick} disabled={disabled}>
      <span className="tracking-tight">{children}</span>
      {icon && (
        <ArrowUpRight
          size={14}
          weight="bold"
          className="transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-px"
        />
      )}
    </button>
  );
}

/* ---------------------------------------------------------------------------
   Severity + status chips — form encodes state (soft-skill: state at a glance)
--------------------------------------------------------------------------- */
const sevSkin: Record<Severity, string> = {
  critical: "bg-sev-crit/12 text-sev-crit border-sev-crit/25",
  high: "bg-sev-warn/12 text-sev-warn border-sev-warn/25",
  medium: "bg-sev-info/12 text-sev-info border-sev-info/25",
  low: "bg-white/[0.06] text-ink-2 border-line-strong",
};
export function SevChip({ sev }: { sev: Severity }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-medium uppercase tracking-wider ${sevSkin[sev]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {sev}
    </span>
  );
}

const statusLabel: Record<SituationStatus, string> = {
  detected: "Detected",
  diagnosed: "Diagnosed",
  acting: "Remediating",
  resolved: "Resolved",
  failed: "Failed",
  needs_attention: "Needs Attention",
  suppressed: "Suppressed",
};
const statusSkin: Record<SituationStatus, string> = {
  detected: "text-ink-2 bg-white/[0.06]",
  diagnosed: "text-sev-info bg-sev-info/10",
  acting: "text-sev-warn bg-sev-warn/10",
  resolved: "text-sev-ok bg-sev-ok/10",
  failed: "text-sev-crit bg-sev-crit/10",
  // Its own tone plus a ring the other chips don't carry — an escalation must
  // never be mistaken for `acting` (in flight) or `failed` (the fix broke).
  needs_attention: "text-sev-attention bg-sev-attention/10 ring-1 ring-inset ring-sev-attention/35",
  suppressed: "text-signal bg-signal/10",
};
export function StatusChip({ status }: { status: SituationStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono text-2xs ${statusSkin[status]}`}>
      {statusLabel[status]}
    </span>
  );
}

/* ---------------------------------------------------------------------------
   Sparkline — area fill + faint grid + emphasized endpoint
--------------------------------------------------------------------------- */
export function Sparkline({
  data,
  color = "#52A8FF",
  height = 44,
  width = 160,
}: {
  data: number[];
  color?: string;
  height?: number;
  width?: number;
}) {
  // An empty or single-point series used to throw (data[length-1] on []) or
  // emit NaN coordinates. Render nothing rather than a broken path.
  if (!data || data.length < 2) return null;
  const max = Math.max(...data);
  const min = Math.min(...data);
  const span = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / span) * (height - 8) - 4;
    return [x, y] as const;
  });
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  const last = pts[pts.length - 1];
  const id = `g${Math.round(width)}${color.replace("#", "")}`;
  return (
    <svg width={width} height={height} className="overflow-visible" aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="white" strokeOpacity="0.07" />
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r="3" fill={color} />
      <circle cx={last[0]} cy={last[1]} r="6" fill={color} fillOpacity="0.25" />
    </svg>
  );
}

/* ---------------------------------------------------------------------------
   Animated count-up number
--------------------------------------------------------------------------- */
export function timeAgo(ts: number | string): string {
  const ms = typeof ts === "number" ? ts : new Date(ts).getTime();
  if (!Number.isFinite(ms)) return "—"; // never render NaN
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export { motion };
