import { useEffect, useState } from "react";

export type Toast = { id: number; kind: "success" | "error"; msg: string };

let _id = 0;
const _listeners = new Set<(t: Toast) => void>();

export function pushToast(kind: Toast["kind"], msg: string) {
  const t = { id: ++_id, kind, msg };
  _listeners.forEach((l) => l(t));
}

export function ToastHost() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => {
    const on = (t: Toast) => {
      setToasts((cur) => [...cur, t]);
      setTimeout(() => setToasts((cur) => cur.filter((x) => x.id !== t.id)), 4000);
    };
    _listeners.add(on);
    return () => { _listeners.delete(on); };
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-[60] flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto rounded-2xl border px-4 py-3 text-sm shadow-lift backdrop-blur-xl transition-all duration-500 ease-fluid ${
            t.kind === "error"
              ? "border-sev-crit/30 bg-sev-crit/10 text-sev-crit"
              : "border-sev-ok/30 bg-sev-ok/10 text-sev-ok"
          }`}
        >
          {t.msg}
        </div>
      ))}
    </div>
  );
}
