type Page = "landing" | "audit-log" | "product" | "docs" | "dashboard";

const INTEGRATIONS = [
  { name: "Prometheus",    mono: "remote_write" },
  { name: "Grafana",       mono: "datasource API" },
  { name: "OpenTelemetry", mono: "otel-collector" },
  { name: "PagerDuty",     mono: "events v2" },
  { name: "Slack",         mono: "webhooks" },
  { name: "AWS CloudWatch",mono: "aws-sdk" },
  { name: "Datadog Agent", mono: "dd-agent forwarder" },
  { name: "Kubernetes",    mono: "kube-state-metrics" },
  { name: "OpsGenie",      mono: "alert API" },
];

function FeatureBlock({
  eyebrow, title, body, bullets, flip, mockup,
}: {
  eyebrow: string; title: string; body: string; bullets: string[];
  flip?: boolean; mockup: React.ReactNode;
}) {
  return (
    <div className="pg-feature" style={{
      display: "grid",
      gridTemplateColumns: flip ? "1fr 480px" : "480px 1fr",
      gap: 72, alignItems: "center",
      padding: "88px 0",
      borderBottom: "1px solid var(--pg-line)",
    }}>
      {flip ? (
        <>
          <div>
            <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "var(--pg-accent)", fontWeight: 500, marginBottom: 12, letterSpacing: "0.04em" }}>{eyebrow}</div>
            <h2 style={{ fontSize: "clamp(24px,3vw,34px)", fontWeight: 800, color: "var(--pg-ink)", letterSpacing: "-0.03em", lineHeight: 1.15, margin: "0 0 16px" }}>{title}</h2>
            <p style={{ fontSize: 15, color: "var(--pg-ink-3)", lineHeight: 1.7, margin: "0 0 24px" }}>{body}</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {bullets.map((b, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <div style={{ width: 18, height: 18, borderRadius: "50%", background: "var(--pg-accent-soft)", color: "var(--pg-accent)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 1 }}>
                    <svg width="9" height="9" viewBox="0 0 9 9" fill="none"><path d="M1.5 4.5l2 2 4-4" stroke="var(--pg-accent)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </div>
                  <span style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.55 }}>{b}</span>
                </div>
              ))}
            </div>
          </div>
          <div>{mockup}</div>
        </>
      ) : (
        <>
          <div>{mockup}</div>
          <div>
            <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "var(--pg-accent)", fontWeight: 500, marginBottom: 12, letterSpacing: "0.04em" }}>{eyebrow}</div>
            <h2 style={{ fontSize: "clamp(24px,3vw,34px)", fontWeight: 800, color: "var(--pg-ink)", letterSpacing: "-0.03em", lineHeight: 1.15, margin: "0 0 16px" }}>{title}</h2>
            <p style={{ fontSize: 15, color: "var(--pg-ink-3)", lineHeight: 1.7, margin: "0 0 24px" }}>{body}</p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {bullets.map((b, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                  <div style={{ width: 18, height: 18, borderRadius: "50%", background: "var(--pg-accent-soft)", color: "var(--pg-accent)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 1 }}>
                    <svg width="9" height="9" viewBox="0 0 9 9" fill="none"><path d="M1.5 4.5l2 2 4-4" stroke="var(--pg-accent)" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
                  </div>
                  <span style={{ fontSize: 14, color: "var(--pg-ink-2)", lineHeight: 1.55 }}>{b}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─── Mockup cards ─────────────────────────────────────────────────────────────

function AnomalyMockup() {
  const base = [42,44,43,45,46,44,43,45,44,46,45,44,43,45,46,82,94,88,76,68,62,58,54,51,48];
  const W = 420; const H = 140;
  const max = Math.max(...base); const min = 40; const range = max - min;
  const px = (i: number) => 12 + (i / (base.length - 1)) * (W - 24);
  const py = (v: number) => 16 + (1 - (v - min) / range) * (H - 36);
  const baselineY = py(46);

  return (
    <div style={{ background: "var(--pg-card)", border: "1px solid var(--pg-line)", borderRadius: 12, overflow: "hidden", boxShadow: "0 2px 16px rgba(16,17,20,0.06)" }}>
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--pg-line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--pg-ink)" }}>CPU Utilization</span>
          <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "var(--pg-ink-4)", marginLeft: 8 }}>api-gateway · 6h window</span>
        </div>
        <span style={{ background: "var(--pg-crit-soft)", color: "var(--pg-crit-ink)", fontSize: 9, fontFamily: "JetBrains Mono, monospace", fontWeight: 700, padding: "2px 7px", borderRadius: 5 }}>ANOMALY +4.2σ</span>
      </div>
      <div style={{ padding: "8px" }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`}>
          <defs>
            <linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--pg-accent)" stopOpacity="0.12" />
              <stop offset="100%" stopColor="var(--pg-accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {/* Baseline band */}
          <rect x="12" y={baselineY - 8} width={W - 24} height="16" fill="var(--pg-accent-soft)" opacity="0.7" rx="2" />
          <text x="14" y={baselineY + 4} fontSize="8" fontFamily="JetBrains Mono,monospace" fill="var(--pg-indigo)" opacity="0.8">baseline</text>
          {/* Anomaly zone */}
          <rect x={px(14)} y="16" width={px(20) - px(14)} height={H - 36} fill="var(--pg-crit-soft)" opacity="0.5" rx="2" />
          {/* Normal line */}
          <polyline points={base.slice(0, 15).map((v, i) => `${px(i)},${py(v)}`).join(" ")}
            fill="none" stroke="var(--pg-accent)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
          {/* Anomaly line */}
          <polyline points={base.slice(14).map((v, i) => `${px(i + 14)},${py(v)}`).join(" ")}
            fill="none" stroke="var(--pg-crit)" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="4 2" />
          {/* Peak marker */}
          <circle cx={px(16)} cy={py(94)} r="4" fill="var(--pg-crit)" />
          <circle cx={px(16)} cy={py(94)} r="8" fill="none" stroke="var(--pg-crit)" strokeWidth="1" opacity="0.4" />
          {/* Grid */}
          {[0.33, 0.66].map((f, i) => (
            <line key={i} x1="12" y1={16 + f * (H - 36)} x2={W - 12} y2={16 + f * (H - 36)} stroke="var(--pg-subtle)" strokeWidth="0.75" />
          ))}
        </svg>
      </div>
      <div style={{ padding: "10px 16px", borderTop: "1px solid var(--pg-line)", fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "var(--pg-ink-4)" }}>
        Detected at 14:21 UTC · 8s from ingest to alert
      </div>
    </div>
  );
}

function RootCauseMockup() {
  const steps = [
    { from: "deploy api-v2.3.1", to: "missing index", arrow: true },
    { from: "query q_8f3a91", to: "holds 22 connections", arrow: true },
    { from: "pool starvation", to: "P99 ↑ 2340ms", arrow: true },
  ];
  return (
    <div style={{ background: "var(--pg-card)", border: "1px solid var(--pg-line)", borderRadius: 12, overflow: "hidden", boxShadow: "0 2px 16px rgba(16,17,20,0.06)" }}>
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--pg-line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--pg-ink)" }}>AI Root Cause Chain — INC-2847</span>
        <span style={{ background: "var(--pg-accent-soft)", color: "var(--pg-indigo-ink)", fontSize: 9, fontFamily: "JetBrains Mono, monospace", fontWeight: 700, padding: "2px 7px", borderRadius: 5 }}>CONFIDENCE 94%</span>
      </div>
      <div style={{ padding: "16px" }}>
        {steps.map((s, i) => (
          <div key={i}>
            <div style={{ display: "flex", gap: 10 }}>
              <div style={{
                flex: 1, background: "var(--pg-page)", border: "1px solid var(--pg-line)", borderRadius: 7,
                padding: "8px 12px", fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "var(--pg-ink-2)",
              }}>{s.from}</div>
              <div style={{ display: "flex", alignItems: "center", color: "var(--pg-ink-4)", fontSize: 14 }}>→</div>
              <div style={{
                flex: 1, background: i === steps.length - 1 ? "var(--pg-crit-soft)" : "var(--pg-page)",
                border: `1px solid ${i === steps.length - 1 ? "var(--pg-crit-line)" : "var(--pg-line)"}`,
                borderRadius: 7, padding: "8px 12px",
                fontFamily: "JetBrains Mono, monospace", fontSize: 11,
                color: i === steps.length - 1 ? "var(--pg-crit-ink)" : "var(--pg-ink-2)",
              }}>{s.to}</div>
            </div>
            {i < steps.length - 1 && (
              <div style={{ display: "flex", justifyContent: "center", padding: "4px 0", color: "var(--pg-line)", fontSize: 16 }}>↓</div>
            )}
          </div>
        ))}
        <div style={{ marginTop: 14, padding: "10px 12px", background: "var(--pg-warn-soft)", border: "1px solid var(--pg-warn-line)", borderRadius: 7 }}>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 9, color: "var(--pg-warn-ink)", marginBottom: 4, fontWeight: 700 }}>RECOMMENDED ACTION</div>
          <div style={{ fontSize: 12, color: "var(--pg-ink-2)" }}>
            Rollback <code style={{ fontFamily: "JetBrains Mono, monospace", background: "var(--pg-warn-soft)", padding: "1px 4px", borderRadius: 3 }}>api-v2.3.1</code> or run <code style={{ fontFamily: "JetBrains Mono, monospace", background: "var(--pg-warn-soft)", padding: "1px 4px", borderRadius: 3 }}>CREATE INDEX CONCURRENTLY</code>
          </div>
        </div>
      </div>
    </div>
  );
}

function ObservabilityMockup() {
  const sources = [
    { label: "Prometheus", count: "4.2M series", color: "var(--pg-crit)" },
    { label: "OpenTelemetry", count: "18k spans/s", color: "var(--pg-indigo)" },
    { label: "Loki (logs)", count: "340k lines/min", color: "var(--pg-ok)" },
    { label: "Events", count: "deploys · alerts", color: "var(--pg-warn)" },
  ];
  return (
    <div style={{ background: "var(--pg-card)", border: "1px solid var(--pg-line)", borderRadius: 12, overflow: "hidden", boxShadow: "0 2px 16px rgba(16,17,20,0.06)" }}>
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--pg-line)" }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--pg-ink)" }}>Data Sources — unified ingestion</span>
      </div>
      <div style={{ padding: "12px" }}>
        {sources.map((s, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 10px", borderRadius: 7, marginBottom: 4, background: "var(--pg-page)", border: "1px solid var(--pg-line)" }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: s.color, flexShrink: 0 }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--pg-ink)", flex: 1 }}>{s.label}</span>
            <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "var(--pg-ink-3)" }}>{s.count}</span>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--pg-ok)" }} />
          </div>
        ))}
        <div style={{ marginTop: 10, padding: "8px 10px", background: "var(--pg-accent-soft)", borderRadius: 7, border: "1px solid var(--pg-accent-line)" }}>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 9, color: "var(--pg-indigo-ink)", fontWeight: 700, marginBottom: 3 }}>CORRELATION ENGINE</div>
          <div style={{ fontSize: 11, color: "var(--pg-ink-2)" }}>Cross-source signal correlation · sub-second · no sampling</div>
        </div>
      </div>
    </div>
  );
}

export default function Product({ onNavigate }: { onNavigate: (p: Page) => void }) {
  return (
    <div style={{ background: "var(--pg-page)", paddingTop: 60 }}>
      {/* Hero */}
      <section className="pg-section" style={{ background: "var(--pg-card)", borderBottom: "1px solid var(--pg-line)", padding: "80px 40px 72px" }}>
        <div style={{ maxWidth: 1100, margin: "0 auto" }}>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "var(--pg-accent)", fontWeight: 500, marginBottom: 16, letterSpacing: "0.06em", textAlign: "center" }}>
            PRODUCT OVERVIEW
          </div>
          <h1 style={{
            fontSize: "clamp(36px, 5vw, 56px)", fontWeight: 800, color: "var(--pg-ink)",
            letterSpacing: "-0.04em", lineHeight: 1.08, maxWidth: 700,
            margin: "0 auto 20px", textAlign: "center",
          }}>
            The AIOps platform engineers actually trust
          </h1>
          <p style={{ fontSize: 17, color: "var(--pg-ink-3)", maxWidth: 520, margin: "0 auto 40px", textAlign: "center", lineHeight: 1.65 }}>
            Built on Prometheus and OpenTelemetry. Deployed in production at companies monitoring thousands of services. Designed by and for SRE teams.
          </p>
          <div style={{ display: "flex", justifyContent: "center", gap: 12 }}>
            <button onClick={() => onNavigate("dashboard")} style={{
              padding: "12px 24px", background: "var(--pg-accent)", color: "var(--pg-on-accent)",
              borderRadius: 10, border: "none", fontSize: 14, fontWeight: 600, cursor: "pointer",
            }}>
              See the live dashboard →
            </button>
            <button style={{
              padding: "12px 24px", background: "var(--pg-card)", color: "var(--pg-ink)",
              borderRadius: 10, border: "1px solid var(--pg-line)", fontSize: 14, fontWeight: 600, cursor: "pointer",
            }}>
              Read the docs
            </button>
          </div>
        </div>
      </section>

      {/* Feature blocks */}
      <div className="pg-section" style={{ maxWidth: 1100, margin: "0 auto", padding: "0 40px" }}>
        <FeatureBlock
          eyebrow="ANOMALY DETECTION"
          title={"Your infrastructure has a baseline.\nWe learn it — precisely."}
          body="IntelliOps builds a rolling statistical model of each metric across every service. Not just simple thresholds — seasonality-aware, deploy-aware, and self-updating. When something deviates beyond what the model predicts, you hear about it. Not before."
          bullets={[
            "7-day warm-up with rolling window updates — no cold start guessing",
            "Seasonality-aware: accounts for day-of-week and time-of-day patterns",
            "Deploy-annotated: new releases trigger model recalibration automatically",
            "Zero manual threshold tuning required — ever",
          ]}
          mockup={<AnomalyMockup />}
        />
        <FeatureBlock
          flip
          eyebrow="ROOT CAUSE AI"
          title={"From 40 alerts to one root cause in seconds."}
          body="When incidents happen, IntelliOps traces the causal chain across services — correlating metrics, traces, logs, and deployment events. You get a structured root-cause report, not a wall of firing alerts, before your on-call engineer has finished opening their laptop."
          bullets={[
            "Causal chain analysis across service boundaries — not just symptom detection",
            "Deployment correlation: links anomalies to the commit that caused them",
            "AI confidence scores with clear reasoning — no black-box outputs",
            "65% faster mean time to root cause vs. manual investigation",
          ]}
          mockup={<RootCauseMockup />}
        />
        <FeatureBlock
          eyebrow="UNIFIED OBSERVABILITY"
          title={"Prometheus. Grafana. OpenTelemetry. All in one place."}
          body="IntelliOps ingests from your existing stack without replacement. Prometheus remote_write, OpenTelemetry collectors, Loki logs, and deployment events are unified into a single correlation layer — giving the AI full context, and giving your team a single workspace."
          bullets={[
            "Native Prometheus remote_write — no agents to swap out",
            "OpenTelemetry-native trace ingestion at scale",
            "Grafana-compatible dashboards: migrate your existing panels with zero changes",
            "Event timeline overlays: deploys, incidents, and config changes on every chart",
          ]}
          mockup={<ObservabilityMockup />}
        />
      </div>

      {/* Integrations */}
      <section className="pg-section" style={{ background: "var(--pg-card)", borderTop: "1px solid var(--pg-line)", padding: "72px 40px" }}>
        <div style={{ maxWidth: 1100, margin: "0 auto" }}>
          <div style={{ textAlign: "center", marginBottom: 48 }}>
            <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "var(--pg-accent)", marginBottom: 10, letterSpacing: "0.04em" }}>INTEGRATIONS</div>
            <h2 style={{ fontSize: 32, fontWeight: 800, color: "var(--pg-ink)", letterSpacing: "-0.03em", margin: 0 }}>
              Works with your stack. No rip-and-replace.
            </h2>
          </div>
          <div className="pg-grid-3" style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            {INTEGRATIONS.map((intg, i) => (
              <div key={i} style={{
                background: "var(--pg-page)", border: "1px solid var(--pg-line)", borderRadius: 10,
                padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center",
                transition: "border-color 0.15s",
              }}
                onMouseEnter={e => (e.currentTarget.style.borderColor = "var(--pg-accent-line)")}
                onMouseLeave={e => (e.currentTarget.style.borderColor = "var(--pg-line)")}
              >
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--pg-ink)" }}>{intg.name}</span>
                <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "var(--pg-ink-4)" }}>{intg.mono}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="pg-section" style={{ background: "linear-gradient(135deg, #1A3AD8 0%, #2F5FF6 60%, #4353FF 100%)", padding: "80px 40px" }}>
        <div style={{ maxWidth: 560, margin: "0 auto", textAlign: "center" }}>
          <h2 style={{ fontSize: 36, fontWeight: 800, color: "var(--pg-on-accent)", letterSpacing: "-0.03em", margin: "0 0 14px" }}>
            Ready to see it live?
          </h2>
          <p style={{ fontSize: 16, color: "rgba(255,255,255,0.75)", margin: "0 0 36px", lineHeight: 1.65 }}>
            Open the demo dashboard and explore a real incident in progress — no login required.
          </p>
          <button onClick={() => onNavigate("dashboard")} style={{
            padding: "13px 28px", background: "var(--pg-card)", color: "var(--pg-accent)",
            borderRadius: 10, border: "none", fontSize: 15, fontWeight: 700, cursor: "pointer",
            boxShadow: "0 2px 12px rgba(0,0,0,0.15)",
          }}>
            Open Demo Dashboard →
          </button>
        </div>
      </section>
    </div>
  );
}
