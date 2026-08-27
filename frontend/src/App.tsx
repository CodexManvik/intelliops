import { useState } from "react";
import { Shell, type View } from "./components/Shell";
import { Overview } from "./views/Overview";
import { Incidents } from "./views/Incidents";
import { Pipeline } from "./views/Pipeline";
import { Governance } from "./views/Governance";
import { Audit } from "./views/Audit";
import { System } from "./views/System";
import "./styles/view.css";

export default function App() {
  const [view, setView] = useState<View>("overview");

  // The view mounts at full opacity (no Framer mount animation — that strands
  // at opacity 0 under StrictMode's double-invoke). Entrance polish comes from
  // a CSS keyframe on the keyed wrapper plus the per-section whileInView reveals
  // inside each view, which are unaffected.
  return (
    <Shell view={view} onView={setView}>
      <div key={view} className="view-enter">
        {view === "overview" && <Overview />}
        {view === "incidents" && <Incidents />}
        {view === "pipeline" && <Pipeline />}
        {view === "governance" && <Governance />}
        {view === "audit" && <Audit />}
        {view === "system" && <System />}
      </div>
    </Shell>
  );
}
