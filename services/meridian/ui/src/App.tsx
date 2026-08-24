import { useCallback, useEffect, useState } from "react";
import AppShell from "./components/AppShell";
import Dashboard from "./views/Dashboard";
import Submit from "./views/Submit";
import Reports from "./views/Reports";
import Operations from "./views/Operations";
import useBackgroundTraffic from "./data/useBackgroundTraffic";

export type View = "dashboard" | "submit" | "reports" | "operations";

const VALID_VIEWS: View[] = ["dashboard", "submit", "reports", "operations"];

function viewFromHash(): View {
  const raw = window.location.hash.replace(/^#\/?/, "") as View;
  return VALID_VIEWS.includes(raw) ? raw : "dashboard";
}

export default function App() {
  const [view, setView] = useState<View>(viewFromHash());
  const [liveTrafficOn, setLiveTrafficOn] = useState(false);
  const { lastTick } = useBackgroundTraffic(liveTrafficOn);

  useEffect(() => {
    const onHashChange = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((next: View) => {
    window.location.hash = `/${next}`;
    setView(next);
  }, []);

  return (
    <AppShell active={view} onNavigate={navigate} liveTrafficOn={liveTrafficOn}>
      <div className="mb-6 flex items-center justify-end">
        <label className="flex items-center gap-2 text-sm text-ink-2">
          <span className="text-2xs font-semibold uppercase tracking-wide text-ink-3">
            Background traffic
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={liveTrafficOn}
            onClick={() => setLiveTrafficOn((v) => !v)}
            className={`relative h-5 w-9 shrink-0 rounded-full transition-colors duration-150 ${
              liveTrafficOn ? "bg-brand" : "bg-line-strong"
            }`}
          >
            <span
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-card transition-transform duration-150 ${
                liveTrafficOn ? "translate-x-[18px]" : "translate-x-0.5"
              }`}
            />
          </button>
        </label>
      </div>

      {view === "dashboard" && <Dashboard lastTick={lastTick} />}
      {view === "submit" && <Submit />}
      {view === "reports" && <Reports lastTick={lastTick} />}
      {view === "operations" && <Operations />}
    </AppShell>
  );
}
