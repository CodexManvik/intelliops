import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Pulse, ShieldCheck, SquaresFour, Waveform } from "@phosphor-icons/react";
import { fluid } from "./primitives";

export type View = "overview" | "incidents" | "governance";

const tabs: { id: View; label: string; icon: JSX.Element }[] = [
  { id: "overview", label: "Overview", icon: <SquaresFour size={17} weight="light" /> },
  { id: "incidents", label: "Incidents", icon: <Waveform size={17} weight="light" /> },
  { id: "governance", label: "Governance", icon: <ShieldCheck size={17} weight="light" /> },
];

export function Shell({
  view,
  onView,
  children,
}: {
  view: View;
  onView: (v: View) => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="grain relative min-h-[100dvh]">
      {/* ambient field */}
      <div className="mesh pointer-events-none fixed inset-0 z-0" aria-hidden />

      {/* Fluid-island nav — floating glass pill, detached from the top */}
      <div className="sticky top-0 z-40 flex justify-center px-4 pt-5">
        <nav className="flex w-full max-w-5xl items-center gap-3 rounded-full border border-white/[0.08] bg-ground/70 px-3 py-2 backdrop-blur-2xl">
          <div className="flex items-center gap-2 pl-1.5 pr-1">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-beat rounded-full bg-signal" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-signal shadow-[0_0_10px_#3DD6D0]" />
            </span>
            <span className="text-sm font-semibold tracking-tight">IntelliOps</span>
            <span className="hidden text-2xs font-medium uppercase tracking-[0.18em] text-ink-3 sm:inline">Control Plane</span>
          </div>

          {/* desktop tabs */}
          <div className="ml-auto hidden items-center gap-1 md:flex">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => onView(t.id)}
                className="relative rounded-full px-4 py-2 text-sm text-ink-2 transition-colors duration-300 hover:text-ink"
              >
                {view === t.id && (
                  <motion.span
                    layoutId="tabpill"
                    className="absolute inset-0 rounded-full bg-white/[0.08] ring-1 ring-white/[0.06]"
                    transition={{ type: "spring", stiffness: 380, damping: 32 }}
                  />
                )}
                <span className={`relative flex items-center gap-2 ${view === t.id ? "text-ink" : ""}`}>
                  {t.icon}
                  {t.label}
                </span>
              </button>
            ))}
          </div>

          {/* live status pill */}
          <div className="ml-auto hidden items-center gap-2 rounded-full border border-white/[0.07] bg-white/[0.03] px-3 py-1.5 md:flex">
            <Pulse size={15} weight="light" className="text-signal" />
            <span className="font-mono text-2xs text-ink-2">6/6 healthy</span>
          </div>

          {/* mobile hamburger → fluid X */}
          <button
            onClick={() => setOpen((o) => !o)}
            className="ml-auto flex h-9 w-9 items-center justify-center rounded-full bg-white/[0.05] md:hidden"
            aria-label="Menu"
          >
            <div className="relative h-3.5 w-4">
              <motion.span
                className="absolute left-0 top-0 h-[1.5px] w-4 rounded-full bg-ink"
                animate={open ? { rotate: 45, y: 6.5 } : { rotate: 0, y: 0 }}
                transition={{ duration: 0.45, ease: fluid }}
              />
              <motion.span
                className="absolute left-0 top-[6.5px] h-[1.5px] w-4 rounded-full bg-ink"
                animate={open ? { opacity: 0 } : { opacity: 1 }}
                transition={{ duration: 0.25 }}
              />
              <motion.span
                className="absolute bottom-0 left-0 h-[1.5px] w-4 rounded-full bg-ink"
                animate={open ? { rotate: -45, y: -6.5 } : { rotate: 0, y: 0 }}
                transition={{ duration: 0.45, ease: fluid }}
              />
            </div>
          </button>
        </nav>
      </div>

      {/* mobile overlay menu — staggered mask reveal */}
      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-ground/85 backdrop-blur-3xl md:hidden"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            {tabs.map((t, i) => (
              <motion.button
                key={t.id}
                onClick={() => {
                  onView(t.id);
                  setOpen(false);
                }}
                initial={{ opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.08 + i * 0.06, duration: 0.5, ease: fluid }}
                className={`flex items-center gap-3 rounded-full px-6 py-3 text-2xl font-medium tracking-tight ${
                  view === t.id ? "text-signal" : "text-ink"
                }`}
              >
                {t.icon}
                {t.label}
              </motion.button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* view content */}
      <main className="relative z-10 mx-auto w-full max-w-6xl px-4 pb-24 pt-8 sm:px-6">{children}</main>
    </div>
  );
}
