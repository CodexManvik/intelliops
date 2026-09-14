import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { CheckCircle, WarningCircle } from "@phosphor-icons/react";

export type Toast = { id: number; kind: "success" | "error"; msg: string };

let _id = 0;
const _listeners = new Set<(t: Toast) => void>();

export function pushToast(kind: Toast["kind"], msg: string) {
  const t = { id: ++_id, kind, msg };
  _listeners.forEach((l) => l(t));
}

/* ---------------------------------------------------------------------------
   Toasts are the ONLY confirmation the operator gets that Approve or Reject
   was accepted, so they previously hard-popped into the corner and vanished
   with no motion and no announcement. Now they slide in, are announced to
   assistive tech via aria-live, and honour prefers-reduced-motion.

   role="status" (polite) rather than "alert" for success; errors get assertive,
   because a failed approval is something the operator must not miss.
--------------------------------------------------------------------------- */

const DURATION_MS = 4500;

export function ToastHost() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const reduce = useReducedMotion();

  useEffect(() => {
    const timers: number[] = [];
    const on = (t: Toast) => {
      setToasts((cur) => [...cur, t]);
      timers.push(
        window.setTimeout(
          () => setToasts((cur) => cur.filter((x) => x.id !== t.id)),
          DURATION_MS,
        ),
      );
    };
    _listeners.add(on);
    return () => {
      _listeners.delete(on);
      timers.forEach(window.clearTimeout);
    };
  }, []);

  return (
    <div
      className="pointer-events-none fixed bottom-6 right-6 z-[60] flex flex-col gap-2"
      aria-live="polite"
      aria-relevant="additions"
    >
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            aria-live={t.kind === "error" ? "assertive" : "polite"}
            initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24, scale: 0.96 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, x: 0, scale: 1 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, x: 16, scale: 0.98 }}
            transition={
              reduce
                ? { duration: 0.15 }
                : { type: "spring", stiffness: 420, damping: 34, mass: 0.7 }
            }
            layout={!reduce}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-2xl border px-4 py-3 text-sm shadow-lift backdrop-blur-xl ${
              t.kind === "error"
                ? "border-sev-crit/30 bg-sev-crit/10 text-sev-crit"
                : "border-sev-ok/30 bg-sev-ok/10 text-sev-ok"
            }`}
          >
            <span className="mt-px flex-none">
              {t.kind === "error" ? (
                <WarningCircle size={16} weight="fill" />
              ) : (
                <CheckCircle size={16} weight="fill" />
              )}
            </span>
            <span className="max-w-[22rem]">{t.msg}</span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
