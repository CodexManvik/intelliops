import { useState } from "react";

type Page = "landing" | "audit-log" | "product" | "docs" | "dashboard";

// ─── Doc tree ─────────────────────────────────────────────────────────────────

interface DocNode {
  id: string;
  label: string;
  children?: DocNode[];
}

const DOC_TREE: DocNode[] = [
  {
    id: "getting-started", label: "Getting Started", children: [
      { id: "quickstart", label: "Quickstart" },
      { id: "installation", label: "Installation" },
      { id: "first-datasource", label: "Connect your first datasource" },
      { id: "understanding-anomalies", label: "Understanding anomaly detection" },
    ],
  },
  {
    id: "configuration", label: "Configuration", children: [
      { id: "prometheus", label: "Prometheus remote_write" },
      { id: "otel-collector", label: "OpenTelemetry collector" },
      { id: "alert-rules", label: "Alert rules & thresholds" },
      { id: "anomaly-sensitivity", label: "Anomaly sensitivity" },
    ],
  },
  {
    id: "integrations", label: "Integrations", children: [
      { id: "pagerduty", label: "PagerDuty" },
      { id: "slack", label: "Slack" },
      { id: "opsgenie", label: "OpsGenie" },
      { id: "grafana", label: "Grafana (import dashboards)" },
      { id: "kubernetes", label: "Kubernetes / kube-state-metrics" },
    ],
  },
  {
    id: "api", label: "API Reference", children: [
      { id: "authentication", label: "Authentication" },
      { id: "incidents-api", label: "Incidents" },
      { id: "alerts-api", label: "Alerts" },
      { id: "metrics-api", label: "Metrics ingestion" },
      { id: "audit-api", label: "Audit log" },
    ],
  },
  {
    id: "concepts", label: "Concepts", children: [
      { id: "baseline-model", label: "Baseline model" },
      { id: "causal-chains", label: "Causal chain analysis" },
      { id: "incident-lifecycle", label: "Incident lifecycle" },
      { id: "alert-deduplication", label: "Alert deduplication" },
    ],
  },
];

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

const TOC = ["Prerequisites", "Install the CLI", "Connect Prometheus", "Verify data flowing", "Your first anomaly alert"];

function QuickstartContent() {
  return (
    <article>
      <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "var(--pg-ink-4)", marginBottom: 8 }}>
        Getting Started / Quickstart
      </div>
      <h1 style={{ fontSize: 28, fontWeight: 800, color: "var(--pg-ink)", letterSpacing: "-0.03em", margin: "0 0 8px", lineHeight: 1.2 }}>
        Quickstart
      </h1>
      <p style={{ fontSize: 14, color: "var(--pg-ink-4)", margin: "0 0 32px", fontFamily: "JetBrains Mono, monospace" }}>
        Last updated · 12 Jan 2024 · 8 min read
      </p>

      <p style={{ fontSize: 15, color: "var(--pg-ink-2)", lineHeight: 1.75, margin: "0 0 20px" }}>
        This guide walks you from zero to a running IntelliOps workspace with your first Prometheus datasource connected, anomaly detection active, and your first alert route configured — in under 15 minutes.
      </p>

      <Callout type="tip">
        If you're migrating from a standalone Grafana + AlertManager setup, see <strong style={{ fontWeight: 600 }}>Migrating from Grafana Alerting</strong> instead — it covers preserving your existing alert rules.
      </Callout>

      <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--pg-ink)", letterSpacing: "-0.02em", margin: "32px 0 12px" }}>Prerequisites</h2>
      <ul style={{ margin: "0 0 20px", padding: "0 0 0 20px", display: "flex", flexDirection: "column", gap: 6 }}>
        {[
          "A running Prometheus instance (v2.40+) with remote_write enabled",
          "Network access from Prometheus to intelliops.app (or a self-hosted endpoint)",
          "An IntelliOps API key — generate one at dashboard → Settings → API Keys",
          "curl or the intelliops CLI (see below)",
        ].map((item, i) => (
          <li key={i} style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.65 }}>{item}</li>
        ))}
      </ul>

      <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--pg-ink)", letterSpacing: "-0.02em", margin: "32px 0 12px" }}>Install the CLI</h2>
      <p style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.7, margin: "0 0 12px" }}>
        The IntelliOps CLI (<code style={{ fontFamily: "JetBrains Mono, monospace", background: "var(--pg-subtle)", padding: "1px 5px", borderRadius: 4, fontSize: 13 }}>io</code>) is the fastest way to configure your workspace and verify your setup.
      </p>
      <Code lang="bash">{`# macOS / Linux
curl -sSL https://intelliops.app/install | sh

# Homebrew
brew install intelliops/tap/io

# Verify installation
io --version
# → io version 1.4.2 (darwin/arm64)`}</Code>

      <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--pg-ink)", letterSpacing: "-0.02em", margin: "32px 0 12px" }}>Connect Prometheus</h2>
      <p style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.7, margin: "0 0 12px" }}>
        Add the following block to your <code style={{ fontFamily: "JetBrains Mono, monospace", background: "var(--pg-subtle)", padding: "1px 5px", borderRadius: 4, fontSize: 13 }}>prometheus.yml</code>. IntelliOps uses standard Prometheus remote_write — no proprietary agents or SDKs.
      </p>
      <Code lang="yaml">{`remote_write:
  - url: https://ingest.intelliops.app/api/v1/write
    bearer_token: IO_YOUR_API_KEY_HERE
    queue_config:
      max_samples_per_send: 10000
      max_shards: 200
      capacity: 2500
    write_relabel_configs:
      - source_labels: [__name__]
        regex: "up|process_.*|node_.*|container_.*"
        action: keep`}</Code>

      <Callout type="warn">
        Replace <code style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12 }}>IO_YOUR_API_KEY_HERE</code> with your actual API key. Never commit API keys to version control — use a secrets manager or environment variable substitution.
      </Callout>

      <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--pg-ink)", letterSpacing: "-0.02em", margin: "32px 0 12px" }}>Verify data flowing</h2>
      <p style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.7, margin: "0 0 12px" }}>
        After restarting Prometheus, verify data is arriving with the CLI:
      </p>
      <Code lang="bash">{`io datasource verify --key IO_YOUR_API_KEY_HERE

# Expected output:
# ✓ Connected to ingest.intelliops.app
# ✓ Receiving metrics: 4,218 series
# ✓ Write lag: 1.2s
# ✓ Anomaly model: warming up (day 1 of 7)`}</Code>

      <Callout type="info">
        The anomaly detection model requires 7 days of baseline data before it reaches full sensitivity. During warm-up, IntelliOps uses conservative thresholds and will notify you when the model is ready.
      </Callout>

      <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--pg-ink)", letterSpacing: "-0.02em", margin: "32px 0 12px" }}>Your first anomaly alert</h2>
      <p style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.7, margin: "0 0 12px" }}>
        Configure where you want alerts delivered. IntelliOps supports PagerDuty, Slack, OpsGenie, and custom webhooks:
      </p>
      <Code lang="bash">{`# Configure Slack
io alert-route create \\
  --name "sre-oncall" \\
  --type slack \\
  --webhook https://hooks.slack.com/services/XXX/YYY/ZZZ \\
  --filter severity=critical,high

# Configure PagerDuty
io alert-route create \\
  --name "pagerduty-prod" \\
  --type pagerduty \\
  --integration-key YOUR_PD_INTEGRATION_KEY \\
  --filter severity=critical`}</Code>
      <p style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.7, margin: "16px 0 0" }}>
        Next: <strong style={{ fontWeight: 600 }}>Configure anomaly sensitivity</strong>
      </p>
    </article>
  );
}

// ─── Docs page ────────────────────────────────────────────────────────────────

export default function Docs({ onNavigate: _onNavigate }: { onNavigate: (p: Page) => void }) {
  const [activeDoc, setActiveDoc] = useState("quickstart");
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(["getting-started"]));

  function toggleSection(id: string) {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="pg-split" style={{ display: "flex", height: "100vh", background: "var(--pg-card)", paddingTop: 60, overflow: "hidden" }}>

      {/* Doc sidebar */}
      <aside className="pg-split-nav" style={{
        width: 240, flexShrink: 0,
        background: "var(--pg-page)", borderRight: "1px solid var(--pg-line)",
        overflowY: "auto", padding: "20px 12px",
      }}>
        {/* Search */}
        <div style={{ position: "relative", marginBottom: 16 }}>
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)" }}>
            <circle cx="5.5" cy="5.5" r="4" stroke="var(--pg-ink-4)" strokeWidth="1.2" />
            <path d="m9 9 2.5 2.5" stroke="var(--pg-ink-4)" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
          <input placeholder="Search docs…" style={{
            width: "100%", paddingLeft: 28, paddingRight: 10, paddingTop: 7, paddingBottom: 7,
            border: "1px solid var(--pg-line)", borderRadius: 7, fontSize: 12, background: "var(--pg-card)",
            fontFamily: "inherit", color: "var(--pg-ink)", outline: "none",
            boxSizing: "border-box",
          }} />
        </div>

        {/* Tree */}
        {DOC_TREE.map(section => (
          <div key={section.id} style={{ marginBottom: 4 }}>
            <button
              onClick={() => toggleSection(section.id)}
              style={{
                width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 8px", borderRadius: 6, background: "none", border: "none",
                cursor: "pointer", fontSize: 12, fontWeight: 700, color: "var(--pg-ink-2)", textAlign: "left",
              }}>
              {section.label}
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
                style={{ transform: expandedSections.has(section.id) ? "rotate(180deg)" : "none", transition: "transform 0.15s", color: "var(--pg-ink-4)" }}>
                <path d="M2 3.5l3 3 3-3" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
              </svg>
            </button>
            {expandedSections.has(section.id) && section.children && (
              <div style={{ paddingLeft: 8 }}>
                {section.children.map(child => {
                  const active = activeDoc === child.id;
                  return (
                    <button key={child.id}
                      onClick={() => setActiveDoc(child.id)}
                      style={{
                        width: "100%", textAlign: "left", padding: "5px 10px", borderRadius: 6,
                        fontSize: 12, fontWeight: active ? 600 : 400,
                        color: active ? "var(--pg-accent)" : "var(--pg-ink-3)",
                        background: active ? "var(--pg-accent-soft)" : "transparent",
                        border: "none", cursor: "pointer", display: "block", marginBottom: 1,
                      }}>
                      {child.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </aside>

      {/* Main content */}
      <div className="pg-split-main" style={{ flex: 1, overflowY: "auto", padding: "40px 56px" }}>
        <div style={{ maxWidth: 720 }}>
          <QuickstartContent />

          {/* Pagination */}
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 48, paddingTop: 24, borderTop: "1px solid var(--pg-line)" }}>
            <div />
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 13, color: "var(--pg-ink-3)" }}>Next:</span>
              <button onClick={() => setActiveDoc("installation")} style={{
                fontSize: 13, fontWeight: 600, color: "var(--pg-accent)", background: "none", border: "none", cursor: "pointer",
                display: "flex", alignItems: "center", gap: 4,
              }}>
                Installation
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <path d="M5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* TOC */}
      <aside className="pg-hide-narrow" style={{ width: 200, flexShrink: 0, padding: "40px 20px", borderLeft: "1px solid var(--pg-line)", overflowY: "auto" }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--pg-ink-4)", letterSpacing: "0.06em", marginBottom: 12, textTransform: "uppercase" }}>
          On this page
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {TOC.map((item, i) => (
            <button key={i} type="button" onClick={() => [...document.querySelectorAll("article h2")].find(h => h.textContent === item)?.scrollIntoView({ block: "start" })} style={{
              border: "none", cursor: "pointer", textAlign: "left", fontFamily: "inherit",
              fontSize: 12, color: i === 0 ? "var(--pg-accent)" : "var(--pg-ink-3)", textDecoration: "none",
              padding: "4px 8px", borderRadius: 5,
              background: i === 0 ? "var(--pg-accent-soft)" : "transparent",
              fontWeight: i === 0 ? 600 : 400,
              transition: "color 0.12s",
            }}
              onMouseEnter={e => { if (i !== 0) (e.currentTarget.style.color = "var(--pg-ink-2)"); }}
              onMouseLeave={e => { if (i !== 0) (e.currentTarget.style.color = "var(--pg-ink-3)"); }}
            >{item}</button>
          ))}
        </div>

        <div style={{ marginTop: 32, padding: "12px 12px", background: "var(--pg-page)", borderRadius: 8, border: "1px solid var(--pg-line)" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--pg-ink-2)", marginBottom: 6 }}>Need help?</div>
          <div style={{ fontSize: 11, color: "var(--pg-ink-3)", lineHeight: 1.55, marginBottom: 8 }}>
            Something unclear or missing? Open an issue on the repository.
          </div>
          <a href="https://github.com/CodexManvik/intelliops/issues" target="_blank" rel="noreferrer" style={{ fontSize: 11, color: "var(--pg-accent)", fontWeight: 600, textDecoration: "none" }}>
            Open an issue →
          </a>
        </div>
      </aside>
    </div>
  );
}
