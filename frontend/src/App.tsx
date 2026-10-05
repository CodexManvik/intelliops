import { lazy, Suspense, useEffect, useState, type ComponentType } from "react";
import { MotionConfig } from "framer-motion";
import { Shell, type View } from "./components/Shell";
import { PageBar, PAGE_LINKS, type PageId } from "./components/PageBar";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Overview } from "./views/Overview";
import { Incidents } from "./views/Incidents";
import { Governance } from "./views/Governance";
import { AgentActivity } from "./views/AgentActivity";
import { System } from "./views/System";
import "./styles/view.css";

/* The five views were in-memory state only, so the browser back button did
   nothing, a view could not be linked to, and a reload always dropped you on
   Overview. A hash route costs ten lines and makes "open Governance" something
   you can send someone. */
const VIEWS: View[] = ["overview", "incidents", "governance", "agent-activity", "settings"];

/* Standalone pages render outside the console Shell, on the same hash router
   (#/dashboard, #/audit-log, #/product, #/docs). Lazy: the console is the
   default route and should not pay for pages most sessions never open. */
type PageProps = { onNavigate: (p: string) => void };
const PAGES: Record<PageId, ComponentType<PageProps>> = {
  dashboard: lazy(() => import("./pages/Dashboard")),
  "audit-log": lazy(() => import("./pages/AuditLog")),
  product: lazy(() => import("./pages/Product")),
  docs: lazy(() => import("./pages/Docs")),
};
const isPage = (r: string): r is PageId => PAGE_LINKS.some((p) => p.id === r);

const hashRoute = () => window.location.hash.replace(/^#\/?/, "");
// The pages call "landing" what the router calls the console's overview.
const navigate = (p: string) => {
  window.location.hash = `#/${p === "landing" ? "overview" : p}`;
};

function viewFromHash(): View {
  const h = hashRoute();
  return (VIEWS as string[]).includes(h) ? (h as View) : "overview";
}

export default function App() {
  const [view, setViewState] = useState<View>(viewFromHash);
  const [route, setRoute] = useState(hashRoute);
  useEffect(() => {
    const onHash = () => {
      setRoute(hashRoute());
      setViewState(viewFromHash());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const setView = (v: View) => {
    // Write the hash first: the hashchange listener above then confirms the
    // state, so browser back/forward and in-app navigation agree.
    window.location.hash = `#/${v}`;
    setViewState(v);
  };
  // Deep-link a specific agent run into the Agent Activity tab — set by
  // Incidents' "Draft a runbook with AI" button, consumed by AgentActivity.
  const [focusRun, setFocusRun] = useState<string | null>(null);

  if (isPage(route)) {
    const Page = PAGES[route];
    return (
      <>
        <PageBar current={route} />
        <ErrorBoundary resetKey={route}>
          <Suspense fallback={<div className="pt-24 text-center text-sm text-ink-3">Loading…</div>}>
            <Page onNavigate={navigate} />
          </Suspense>
        </ErrorBoundary>
      </>
    );
  }

  // The view mounts at full opacity (no Framer mount animation — that strands
  // at opacity 0 under StrictMode's double-invoke). Entrance polish comes from
  // a CSS keyframe on the keyed wrapper plus the per-section whileInView reveals
  // inside each view, which are unaffected.
  // reducedMotion="user" makes EVERY framer-motion animation in the tree honour
  // prefers-reduced-motion. It was previously respected only by CSS keyframes,
  // so motion-sensitive users still got the full spring/layout choreography.
  return (
    <MotionConfig reducedMotion="user">
    <Shell view={view} onView={setView}>
      <div key={view} className="view-enter">
        {/* One view throwing must not blank the console. Keyed on `view` so
            navigating away from a broken panel clears the error. */}
        <ErrorBoundary resetKey={view}>
          {view === "overview" && <Overview onView={setView} />}
          {view === "incidents" && <Incidents onView={setView} onFocusRun={setFocusRun} />}
          {view === "governance" && <Governance />}
          {view === "agent-activity" && <AgentActivity focusRun={focusRun} setFocusRun={setFocusRun} />}
          {view === "settings" && <System />}
        </ErrorBoundary>
      </div>
    </Shell>
    </MotionConfig>
  );
}
