import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Circuitry, ListChecks, SquaresFour, ShieldCheck, Waveform } from "@phosphor-icons/react";
import { fluid } from "./primitives";
import { ThemeToggle } from "./ThemeToggle";
import { ToastHost } from "../hooks/useToast";
import { onConnectionHealth } from "../hooks/useLiveData";
import { WarningCircle } from "@phosphor-icons/react";

export type View = "overview" | "incidents" | "governance" | "agent-activity" | "settings";

const tabs: { id: View; label: string; icon: JSX.Element }[] = [
  { id: "overview", label: "Overview", icon: <SquaresFour size={17} weight="light" /> },
  { id: "incidents", label: "Incidents", icon: <Waveform size={17} weight="light" /> },
  { id: "governance", label: "Governance", icon: <ShieldCheck size={17} weight="light" /> },
  { id: "agent-activity", label: "Agent Activity", icon: <ListChecks size={17} weight="light" /> },
  { id: "settings", label: "Settings", icon: <Circuitry size={17} weight="light" /> },
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
  // A backend that has fallen over used to look exactly like a healthy, quiet
  // fleet: zeros everywhere and a "streaming" badge. Say it out loud instead.
  const [failing, setFailing] = useState(0);
  useEffect(() => onConnectionHealth(setFailing), []);

  return (
    <div className="relative min-h-[100dvh]">
      {/* Top bar. Was a floating translucent pill left over from the light
          theme: it rendered as a white lozenge sitting ON TOP of the first row
          of content. A full-width bar with a single hairline under it is both
          the correct dark-UI idiom and out of the content's way. */}
      <div className="sticky top-0 z-40">
      <AnimatePresence>
        {failing > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            role="alert"
            className="flex items-center justify-center gap-2 overflow-hidden border-b border-sev-crit/25 bg-sev-crit/[0.10] px-4 py-2 text-sm text-sev-crit backdrop-blur-xl"
          >
            <WarningCircle size={15} weight="fill" />
            <span>
              Can't reach the backend — {failing} data source{failing > 1 ? "s" : ""} failing. What
              you see below may be stale.
            </span>
          </motion.div>
        )}
      </AnimatePresence>
      <header className="border-b border-line bg-ground/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-beat rounded-full bg-signal" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-signal" />
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-ink">IntelliOps</span>
            <span className="hidden border-l border-line-strong pl-2.5 text-2xs font-medium uppercase tracking-[0.16em] text-ink-3 sm:inline">
              Control plane
            </span>
          </div>

          {/* desktop tabs — underline indicator, flush with the header rule */}
          <div className="ml-4 hidden h-full items-stretch md:flex">
            {tabs.map((t) => (
              <button
                key={t.id}
                onClick={() => onView(t.id)}
                aria-current={view === t.id ? "page" : undefined}
                className={`relative flex items-center gap-2 px-3.5 text-[13px] transition-colors duration-200 ${
                  view === t.id ? "text-ink" : "text-ink-3 hover:text-ink-2"
                }`}
              >
                {t.icon}
                {t.label}
                {view === t.id && (
                  <motion.span
                    layoutId="tabunderline"
                    className="absolute inset-x-2 -bottom-px h-px bg-ink"
                    transition={{ type: "spring", stiffness: 420, damping: 36 }}
                  />
                )}
              </button>
            ))}
          </div>

          {/* Connection state, derived — not a hardcoded health count. */}
          <div className="ml-auto hidden items-center gap-4 md:flex">
            <div className="flex items-center gap-2">
              <span
                className={`h-1.5 w-1.5 rounded-full ${failing > 0 ? "bg-sev-crit" : "bg-sev-ok"}`}
              />
              <span className="font-mono text-2xs text-ink-3">
                {failing > 0 ? `${failing} source${failing > 1 ? "s" : ""} down` : "streaming"}
              </span>
            </div>
            <ThemeToggle />
          </div>

          {/* mobile hamburger → fluid X */}
          <button
            onClick={() => setOpen((o) => !o)}
            className="ml-auto flex h-9 w-9 items-center justify-center rounded-md border border-line-strong md:hidden"
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
        </div>
      </header>
      </div>

      {/* mobile overlay menu — staggered mask reveal */}
      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-ground/95 backdrop-blur-2xl md:hidden"
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
      <main id="main" className="relative z-10 mx-auto w-full max-w-6xl px-4 pb-24 pt-8 sm:px-6">{children}</main>
      <ToastHost />
    </div>
  );
}
