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
   Double-Bezel card — outer machined shell + inner core with concentric radius
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
    <div
      className={`rounded-4xl p-1.5 border border-black/[0.08] bg-black/[0.02] ${
        glow ? "shadow-glow" : ""
      } ${className}`}
    >
      <div
        className={`rounded-[calc(2rem-6px)] bg-ground-sunken shadow-inset border border-black/[0.06] ${coreClassName}`}
      >
        {children}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Eyebrow tag — microscopic pill preceding headings
--------------------------------------------------------------------------- */
export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-black/[0.08] bg-signal/[0.06] px-3 py-1 text-2xs font-medium uppercase tracking-[0.2em] text-signal-dim">
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------------------
   Magnetic CTA with the button-in-button trailing icon
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
    "group relative inline-flex items-center gap-3 rounded-full pl-6 pr-2 py-2.5 text-sm font-medium transition-all duration-500 ease-fluid active:scale-[0.97] disabled:opacity-40 disabled:active:scale-100";
  const skin =
    variant === "primary"
      ? "bg-signal text-white shadow-[0_8px_24px_-6px_rgba(0,113,227,0.35)] hover:shadow-[0_12px_32px_-6px_rgba(0,113,227,0.45)]"
      : "bg-black/[0.04] text-ink border border-black/[0.10] hover:bg-black/[0.06]";
  return (
    <button className={`${base} ${skin}`} onClick={onClick} disabled={disabled}>
      <span className="tracking-tight">{children}</span>
      {icon && (
        <span
          className={`flex h-8 w-8 items-center justify-center rounded-full transition-transform duration-500 ease-fluid group-hover:translate-x-0.5 group-hover:-translate-y-[1px] group-hover:scale-105 ${
            variant === "primary" ? "bg-white/20" : "bg-black/[0.06]"
          }`}
        >
          <ArrowUpRight size={15} weight="bold" />
        </span>
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
  low: "bg-black/[0.05] text-ink-2 border-black/[0.10]",
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
  suppressed: "Suppressed",
};
const statusSkin: Record<SituationStatus, string> = {
  detected: "text-ink-2 bg-black/[0.05]",
  diagnosed: "text-sev-info bg-sev-info/10",
  acting: "text-sev-warn bg-sev-warn/10",
  resolved: "text-sev-ok bg-sev-ok/10",
  failed: "text-sev-crit bg-sev-crit/10",
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
  color = "#0071E3",
  height = 44,
  width = 160,
}: {
  data: number[];
  color?: string;
  height?: number;
  width?: number;
}) {
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
      <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="black" strokeOpacity="0.08" />
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
