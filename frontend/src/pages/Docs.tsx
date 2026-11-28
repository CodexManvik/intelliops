import { useState } from "react";

type Page = "landing" | "audit-log" | "product" | "docs" | "dashboard";

const REPO = "https://github.com/CodexManvik/intelliops";

// ─── Code block ───────────────────────────────────────────────────────────────

function Code({ lang, children }: { lang: string; children: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ background: "var(--pg-code-bg)", borderRadius: 10, overflow: "hidden", margin: "16px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 16px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "var(--pg-ink-3)" }}>{lang}</span>
        <button
          onClick={() => { void navigator.clipboard?.writeText(children); setCopied(true); setTimeout(() => setCopied(false), 1800); }}
          style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: copied ? "var(--pg-ok)" : "var(--pg-ink-3)", background: "none", border: "none", cursor: "pointer" }}>
          {copied ? "✓ copied" : "copy"}
        </button>
      </div>
      <pre style={{ margin: 0, padding: "16px", overflowX: "auto" }}>
        <code style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#E4E5E9", lineHeight: 1.7, whiteSpace: "pre" }}>
          {children}
        </code>
      </pre>
    </div>
  );
}

function Callout({ type, children }: { type: "info" | "warn" | "tip"; children: React.ReactNode }) {
  const styles = {
    info: { bg: "var(--pg-accent-soft)", border: "var(--pg-accent-line)", icon: "ℹ", color: "var(--pg-indigo-ink)" },
    warn: { bg: "var(--pg-warn-soft)", border: "var(--pg-warn-line)", icon: "⚠", color: "var(--pg-warn-ink)" },
    tip:  { bg: "var(--pg-ok-soft)", border: "var(--pg-ok-line)", icon: "✦", color: "var(--pg-ok-ink)" },
  };
  const s = styles[type];
  return (
    <div style={{ background: s.bg, border: `1px solid ${s.border}`, borderRadius: 8, padding: "12px 14px", margin: "16px 0", display: "flex", gap: 10 }}>
      <span style={{ color: s.color, flexShrink: 0, fontSize: 14 }}>{s.icon}</span>
      <div style={{ fontSize: 13, color: "var(--pg-ink-2)", lineHeight: 1.65 }}>{children}</div>
    </div>
  );
}

// ─── Doc content ──────────────────────────────────────────────────────────────

const H2 = ({ children }: { children: string }) => (
  <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--pg-ink)", letterSpacing: "-0.02em", margin: "32px 0 12px" }}>{children}</h2>
);

const P = ({ children }: { children: React.ReactNode }) => (
  <p style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.7, margin: "0 0 12px" }}>{children}</p>
);

interface Section {
  id: string;
  label: string;
  body: React.ReactNode;
}

const SECTIONS: Section[] = [
  {
    id: "quickstart",
    label: "Quickstart",
    body: (
      <>
        <P>You need Docker with Compose, and Node 20 for the console. One command starts Redis, Postgres, the seven services, Prometheus and the demo workloads.</P>
        <Code lang="bash">{`git clone https://github.com/CodexManvik/intelliops
cd intelliops
docker compose -f deploy/docker-compose.yml up --build`}</Code>
        <H2>Start the console</H2>
        <Code lang="bash">{`cd frontend
npm install
VITE_DATA_MODE=live npm run dev   # http://localhost:5173`}</Code>
        <Callout type="tip">The first build compiles the whole backend and takes several minutes. Later builds are cached.</Callout>
      </>
    ),
  },
  {
    id: "incident",
    label: "Run an incident",
    body: (
      <>
        <P>Break the demo app, wait for detection and diagnosis, then approve the proposed fix in the console.</P>
        <Code lang="bash">{`bash scripts/chaos.sh          # break demo-app and list open incidents
bash scripts/chaos.sh reset    # start again from a clean slate`}</Code>
        <Callout type="info">Remediation is a dry run unless you deploy to Kubernetes with the real remediator switched on.</Callout>
      </>
    ),
  },
  {
    id: "telemetry",
    label: "Send your own telemetry",
    body: (
      <>
        <P>The ingestion service polls Prometheus and also accepts events pushed to it. A batch with any malformed event is refused as a whole.</P>
        <Code lang="bash">{`curl -H 'Content-Type: application/json' http://localhost:8001/ingest \
  -d '{"events":[{"source":"my-app","kind":"metric","name":"error_rate",
                  "value":0.4,"labels":{"service":"checkout"}}]}'
# {"accepted":1}`}</Code>
        <P>Diagnosis reads the <code>service</code> label, so set it on everything you want acted on.</P>
      </>
    ),
  },
  {
    id: "safety",
    label: "Safety model",
    body: (
      <>
        <P>An action must come from a closed list of types, which neither a person nor a language model can extend at run time. Before anything runs, it passes permission checks, a rate limit and a blast-radius limit.</P>
        <P>A new runbook waits for a person to approve it. It can earn automatic execution only after a record of successful runs, and can be demoted again. Decisions are written to the audit log.</P>
      </>
    ),
  },
  {
    id: "services",
    label: "Services and ports",
    body: (
      <>
        <Code lang="text">{`8001  ingestion     telemetry in
8002  correlation   anomaly detection, incident windows
8003  rca           diagnosis and runbook choice
8004  action        safety gates, execution, verification
8005  governance    permissions, approvals, audit, runbooks
8006  feedback      outcomes and graduation
8007  read          what the console reads, live stream`}</Code>
        <P>Each answers <code>/health</code>, <code>/ready</code> and <code>/_metrics</code>.</P>
      </>
    ),
  },
  {
    id: "kubernetes",
    label: "Kubernetes",
    body: (
      <>
        <P>The script creates a local kind cluster, builds the images and installs the Helm chart. Use <code>SAFE=1</code> to keep remediation as a dry run.</P>
        <Code lang="bash">{`SAFE=1 ./scripts/kind-up-full.sh`}</Code>
        <Callout type="warn">Without <code>SAFE=1</code> the platform restarts and scales real pods. Point it only at a cluster you can afford to break, and turn token authentication on before exposing it.</Callout>
      </>
    ),
  },
];

// ─── Docs page ────────────────────────────────────────────────────────────────

export default function Docs({ onNavigate: _onNavigate }: { onNavigate: (p: Page) => void }) {
  const [activeId, setActiveId] = useState(SECTIONS[0].id);
  const index = SECTIONS.findIndex(s => s.id === activeId);
  const active = SECTIONS[index];
  const next = SECTIONS[index + 1];
  const link = { display: "block", marginTop: 8, padding: "0 10px", fontSize: 12, color: "var(--pg-accent)", fontWeight: 600, textDecoration: "none" };

  return (
    <div className="pg-split" style={{ display: "flex", height: "100vh", background: "var(--pg-card)", paddingTop: 60, overflow: "hidden" }}>
      <aside className="pg-split-nav" style={{ width: 240, flexShrink: 0, background: "var(--pg-page)", borderRight: "1px solid var(--pg-line)", overflowY: "auto", padding: "20px 12px" }}>
        {SECTIONS.map(section => {
          const current = section.id === activeId;
          return (
            <button key={section.id} type="button" onClick={() => setActiveId(section.id)} aria-current={current ? "page" : undefined}
              style={{ width: "100%", textAlign: "left", padding: "6px 10px", borderRadius: 6, fontSize: 13, fontWeight: current ? 600 : 400, color: current ? "var(--pg-accent)" : "var(--pg-ink-3)", background: current ? "var(--pg-accent-soft)" : "transparent", border: "none", cursor: "pointer", display: "block", marginBottom: 2 }}>
              {section.label}
            </button>
          );
        })}
        <a href={`${REPO}/tree/master/docs`} target="_blank" rel="noreferrer" style={{ ...link, marginTop: 20 }}>Full documentation →</a>
        <a href={`${REPO}/issues`} target="_blank" rel="noreferrer" style={link}>Open an issue →</a>
      </aside>

      <div className="pg-split-main" style={{ flex: 1, overflowY: "auto", padding: "40px 56px" }}>
        <article style={{ maxWidth: 720 }}>
          <h1 style={{ fontSize: 28, fontWeight: 800, color: "var(--pg-ink)", letterSpacing: "-0.03em", margin: "0 0 24px", lineHeight: 1.2 }}>{active.label}</h1>
          {active.body}
          {next && (
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 48, paddingTop: 24, borderTop: "1px solid var(--pg-line)" }}>
              <button type="button" onClick={() => setActiveId(next.id)} style={{ fontSize: 13, fontWeight: 600, color: "var(--pg-accent)", background: "none", border: "none", cursor: "pointer" }}>
                Next: {next.label} →
              </button>
            </div>
          )}
        </article>
      </div>
    </div>
  );
}
