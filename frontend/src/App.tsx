import { useState } from "react";
import { Shell, type View } from "./components/Shell";
import { Incidents } from "./views/Incidents";
import { Governance } from "./views/Governance";
import { System } from "./views/System";
import "./styles/view.css";

export default function App() {
  const [view, setView] = useState<View>("incidents");

  // The view mounts at full opacity (no Framer mount animation — that strands
  // at opacity 0 under StrictMode's double-invoke). Entrance polish comes from
  // a CSS keyframe on the keyed wrapper plus the per-section whileInView reveals
  // inside each view, which are unaffected.
  return (
    <Shell view={view} onView={setView}>
      <div key={view} className="view-enter">
        {view === "incidents" && <Incidents />}
        {view === "governance" && <Governance />}
        {view === "settings" && <System />}
      </div>
    </Shell>
  );
}
