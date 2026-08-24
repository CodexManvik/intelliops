import type { ReactNode } from "react";
import type { View } from "../App";
import StatusPill from "./StatusPill";

const NAV_ITEMS: { id: View; label: string; hint: string }[] = [
  { id: "dashboard", label: "Dashboard", hint: "Overview" },
  { id: "submit", label: "Submit", hint: "New submission" },
  { id: "reports", label: "Reports", hint: "Generated reports" },
  { id: "operations", label: "Operations", hint: "SRE panel" },
];

export default function AppShell({
  active,
  onNavigate,
  liveTrafficOn,
  children,
}: {
  active: View;
  onNavigate: (view: View) => void;
  liveTrafficOn: boolean;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-surface-subtle">
      {/* Top app-bar */}
      <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-line bg-surface px-6">
        <div className="flex items-center gap-3">
          <MeridianMark />
          <div className="leading-tight">
            <div className="font-serif text-[15px] font-semibold tracking-tight text-ink">
              Meridian
            </div>
            <div className="text-2xs font-medium uppercase tracking-wide text-ink-3">
              Financial Reporting Platform
            </div>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <StatusPill tone={liveTrafficOn ? "ok" : "neutral"} pulse={liveTrafficOn}>
            {liveTrafficOn ? "Live traffic" : "Traffic paused"}
          </StatusPill>
          <div className="flex items-center gap-2 rounded border border-line bg-surface-subtle px-3 py-1.5">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-navy text-2xs font-semibold text-white">
              MT
            </div>
            <div className="text-xs font-medium text-ink-2">M. Talwar</div>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1400px]">
        {/* Left nav */}
        <nav className="sticky top-14 hidden h-[calc(100vh-3.5rem)] w-56 shrink-0 border-r border-line bg-surface px-3 py-6 md:block">
          <div className="mb-3 px-3 text-2xs font-semibold uppercase tracking-wide text-ink-3">
            Workspace
          </div>
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => {
              const isActive = item.id === active;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => onNavigate(item.id)}
                    className={`flex w-full flex-col items-start rounded px-3 py-2 text-left transition-colors duration-150 ${
                      isActive
                        ? "bg-brand-tint text-brand-dim"
                        : "text-ink-2 hover:bg-surface-subtle hover:text-ink"
                    }`}
                  >
                    <span className="text-sm font-medium">{item.label}</span>
                    <span
                      className={`text-2xs ${isActive ? "text-brand-dim/70" : "text-ink-4"}`}
                    >
                      {item.hint}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="mt-8 border-t border-line pt-4 px-3">
            <div className="text-2xs uppercase tracking-wide text-ink-4">Environment</div>
            <div className="mt-1 text-xs font-medium text-ink-2">Production · US-EAST</div>
          </div>
        </nav>

        {/* Mobile nav */}
        <nav className="fixed inset-x-0 bottom-0 z-20 flex border-t border-line bg-surface md:hidden">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              className={`flex-1 py-3 text-2xs font-semibold uppercase tracking-wide ${
                item.id === active ? "text-brand-dim" : "text-ink-3"
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <main className="min-w-0 flex-1 px-6 py-8 pb-20 md:pb-8">{children}</main>
      </div>
    </div>
  );
}

function MeridianMark() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
      <rect width="28" height="28" rx="6" fill="#0E7C5A" />
      <path
        d="M6 20 L11 9 L14 16 L17 9 L22 20"
        stroke="white"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </svg>
  );
}
