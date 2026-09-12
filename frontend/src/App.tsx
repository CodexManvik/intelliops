import { useState } from "react";
import { Shell, type View } from "./components/Shell";
import { Overview } from "./views/Overview";
import { Incidents } from "./views/Incidents";
import { Governance } from "./views/Governance";
import { AgentActivity } from "./views/AgentActivity";
import { System } from "./views/System";
import "./styles/view.css";

export default function App() {
  const [view, setView] = useState<View>("overview");
  // Deep-link a specific agent run into the Agent Activity tab — set by
  // Incidents' "Draft a runbook with AI" button, consumed by AgentActivity.
  const [focusRun, setFocusRun] = useState<string | null>(null);

  // The view mounts at full opacity (no Framer mount animation — that strands
  // at opacity 0 under StrictMode's double-invoke). Entrance polish comes from
  // a CSS keyframe on the keyed wrapper plus the per-section whileInView reveals
  // inside each view, which are unaffected.
  return (
    <Shell view={view} onView={setView}>
      <div key={view} className="view-enter">
        {view === "overview" && <Overview onView={setView} />}
        {view === "incidents" && <Incidents onView={setView} onFocusRun={setFocusRun} />}
        {view === "governance" && <Governance />}
        {view === "agent-activity" && <AgentActivity focusRun={focusRun} setFocusRun={setFocusRun} />}
        {view === "settings" && <System />}
      </div>
    </Shell>
  );
}
