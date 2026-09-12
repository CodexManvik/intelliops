import { useState, useEffect, useRef, useCallback } from "react";
import AuditLog from "./pages/AuditLog";
import Dashboard from "./pages/Dashboard";
import Product from "./pages/Product";
import Docs from "./pages/Docs";

type Page = "landing" | "audit-log" | "product" | "docs" | "dashboard";

// ─── Icons ────────────────────────────────────────────────────────────────────

function IconBell() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <path d="M10 2a6 6 0 0 0-6 6v3l-1.5 2.5h15L16 11V8a6 6 0 0 0-6-6Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M8 16a2 2 0 0 0 4 0" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  );
}

function IconCpu() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <rect x="5" y="5" width="10" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M7.5 7.5h5v5h-5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M7 3v2M10 3v2M13 3v2M7 15v2M10 15v2M13 15v2M3 7h2M3 10h2M3 13h2M15 7h2M15 10h2M15 13h2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

function IconSearch() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <circle cx="9" cy="9" r="5.5" stroke="currentColor" strokeWidth="1.5"/>
      <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

function IconChart() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <path d="M3 15 7 9l3.5 4L14 7l3 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M3 17h14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

function IconArrowRight() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M2.5 7l3 3 6-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

function IconGlobe() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.25"/>
      <path d="M8 2.5c-2 2-2 9 0 11M8 2.5c2 2 2 9 0 11M2.5 8h11" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/>
    </svg>
  );
}

function IconGitHub() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
    </svg>
  );
}

function IconTwitter() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M12.6.75h2.454l-5.36 6.142L16 15.25h-4.937l-3.867-5.07-4.425 5.07H.316l5.733-6.57L0 .75h5.063l3.495 4.633L12.601.75Zm-.86 13.028h1.36L4.323 2.145H2.865z"/>
    </svg>
  );
}

function IconLinkedIn() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
      <path d="M0 1.146C0 .513.526 0 1.175 0h13.65C15.474 0 16 .513 16 1.146v13.708c0 .633-.526 1.146-1.175 1.146H1.175C.526 16 0 15.487 0 14.854V1.146zm4.943 12.248V6.169H2.542v7.225h2.401zm-1.2-8.212c.837 0 1.358-.554 1.358-1.248-.015-.709-.52-1.248-1.342-1.248-.822 0-1.359.54-1.359 1.248 0 .694.521 1.248 1.327 1.248h.016zm4.908 8.212V9.359c0-.216.016-.432.08-.586.173-.431.568-.878 1.232-.878.869 0 1.216.662 1.216 1.634v3.865h2.401V9.25c0-2.22-1.184-3.252-2.764-3.252-1.274 0-1.845.7-2.165 1.193v.025h-.016a5.54 5.54 0 0 1 .016-.025V6.169h-2.4c.03.678 0 7.225 0 7.225h2.4z"/>
    </svg>
  );
}

function IconConnector() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <circle cx="4" cy="10" r="2.5" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="16" cy="10" r="2.5" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M6.5 10h7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeDasharray="2 2"/>
      <circle cx="10" cy="10" r="1.5" fill="currentColor"/>
    </svg>
  );
}

function IconBrain() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
      <path d="M10 3C7.5 3 5.5 5 5.5 7.5c0 1-.3 1.8-.8 2.5C4 11 3.5 12 3.5 13c0 2 1.5 3.5 3.5 3.5h6c2 0 3.5-1.5 3.5-3.5 0-1-.5-2-1.2-2.7-.5-.7-.8-1.5-.8-2.3C14.5 5.5 12.5 3 10 3Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M10 16.5V18M7 16.5v1M13 16.5v1M10 7v4M8 9h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  );
}

// ─── Mini Chart SVG ────────────────────────────────────────────────────────────

function SparklineChart({ color = "#2F5FF6", data }: { color?: string; data: number[] }) {
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const w = 120;
  const h = 40;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = h - ((v - min) / range) * h * 0.85 - h * 0.05;
    return `${x},${y}`;
  });
  const polyline = pts.join(" ");
  const area = `0,${h} ${polyline} ${w},${h}`;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <defs>
        <linearGradient id={`grad-${color.replace("#","")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.2"/>
          <stop offset="100%" stopColor={color} stopOpacity="0"/>
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#grad-${color.replace("#","")})`}/>
      <polyline points={polyline} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round"/>
    </svg>
  );
}

// ─── Hero Dashboard Mockup ─────────────────────────────────────────────────────

function HeroDashboard() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 2000);
    return () => clearInterval(id);
  }, []);

  const alerts = [
    { level: "crit", service: "api-gateway", msg: "P99 latency > 2s", time: "0s ago", anomaly: true },
    { level: "warn", service: "postgres-primary", msg: "Connection pool 89%", time: "12s ago", anomaly: false },
    { level: "info", service: "k8s-scheduler", msg: "Pod rescheduled (OOM)", time: "41s ago", anomaly: false },
    { level: "ok", service: "cdn-edge", msg: "Cache hit rate normalized", time: "2m ago", anomaly: false },
  ];

  const cpuData = [42, 44, 45, 51, 63, 78, 82, 74, 68, 65, 72, 80, 76, 71, 69];
  const latData = [88, 91, 87, 94, 102, 198, 220, 210, 185, 172, 160, 148, 142, 138, 135];
  const reqData = [310, 318, 325, 312, 309, 298, 287, 302, 315, 320, 318, 311, 308, 316, 322];

  return (
    <div className="relative w-full max-w-3xl mx-auto">
      {/* Glow */}
      <div style={{
        position: "absolute", inset: "-48px", zIndex: 0,
        background: "radial-gradient(ellipse 70% 60% at 50% 50%, rgba(47,95,246,0.12) 0%, rgba(67,83,255,0.06) 50%, transparent 100%)",
        filter: "blur(24px)"
      }}/>

      {/* Main card */}
      <div style={{
        position: "relative", zIndex: 1,
        background: "#FFFFFF",
        border: "1px solid #E4E5E9",
        borderRadius: "16px",
        boxShadow: "0 4px 24px rgba(16,17,20,0.06), 0 1px 4px rgba(16,17,20,0.04)",
        overflow: "hidden"
      }}>
        {/* Toolbar */}
        <div style={{ background: "#F7F8FA", borderBottom: "1px solid #E4E5E9", padding: "12px 16px", display: "flex", alignItems: "center", gap: "8px" }}>
          <div style={{ width: 10, height: 10, borderRadius: "50%", background: "#EF4444" }}/>
          <div style={{ width: 10, height: 10, borderRadius: "50%", background: "#F59E0B" }}/>
          <div style={{ width: 10, height: 10, borderRadius: "50%", background: "#22C55E" }}/>
          <div style={{ flex: 1, background: "#EDEEF1", borderRadius: "6px", padding: "4px 10px", marginLeft: "8px", fontFamily: "JetBrains Mono, monospace", fontSize: "11px", color: "#6B7280" }}>
            intelliops.app / dashboard
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "4px", padding: "4px 8px", background: "#DCFCE7", borderRadius: "6px" }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#22C55E", animation: "pulse 2s infinite" }}/>
            <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "10px", color: "#16A34A", fontWeight: 600 }}>LIVE</span>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0", borderBottom: "1px solid #E4E5E9" }}>
          {[
            { label: "CPU Utilization", val: "76%", delta: "+8%", color: "#2F5FF6", data: cpuData, status: "warn" },
            { label: "P99 Latency", val: "135ms", delta: "↓ normalizing", color: "#EF4444", data: latData, status: "crit" },
            { label: "Req / sec", val: "322", delta: "stable", color: "#22C55E", data: reqData, status: "ok" },
          ].map((m, i) => (
            <div key={i} style={{
              padding: "16px",
              borderRight: i < 2 ? "1px solid #E4E5E9" : "none",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                <span style={{ fontSize: "11px", color: "#6B7280", fontFamily: "JetBrains Mono, monospace" }}>{m.label}</span>
                <span style={{
                  fontSize: "9px", fontFamily: "JetBrains Mono, monospace",
                  padding: "2px 6px", borderRadius: "4px",
                  background: m.status === "crit" ? "#FEF2F2" : m.status === "warn" ? "#FFFBEB" : "#F0FDF4",
                  color: m.status === "crit" ? "#EF4444" : m.status === "warn" ? "#F59E0B" : "#22C55E",
                }}>
                  {m.status.toUpperCase()}
                </span>
              </div>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "22px", fontWeight: 600, color: "#101114", marginBottom: "4px" }}>{m.val}</div>
              <div style={{ fontSize: "10px", color: "#6B7280", marginBottom: "10px", fontFamily: "JetBrains Mono, monospace" }}>{m.delta}</div>
              <SparklineChart color={m.color} data={m.data}/>
            </div>
          ))}
        </div>

        {/* Alert feed */}
        <div style={{ padding: "12px 16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
            <span style={{ fontSize: "12px", fontWeight: 600, color: "#101114", letterSpacing: "-0.01em" }}>Alert Feed</span>
            <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "10px", color: "#2F5FF6" }}>AI Root-Cause Active</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {alerts.map((a, i) => (
              <div key={i} style={{
                display: "flex", alignItems: "center", gap: "10px",
                padding: "8px 10px",
                background: i === 0 ? "#FEF2F2" : "#F7F8FA",
                borderRadius: "8px",
                border: i === 0 ? "1px solid #FECACA" : "1px solid #E4E5E9",
              }}>
                <div style={{
                  width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                  background: a.level === "crit" ? "#EF4444" : a.level === "warn" ? "#F59E0B" : a.level === "ok" ? "#22C55E" : "#6B7280"
                }}/>
                <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "10px", color: "#6B7280", minWidth: 90 }}>{a.service}</span>
                <span style={{ fontSize: "11px", color: "#101114", flex: 1 }}>{a.msg}</span>
                {a.anomaly && (
                  <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "9px", background: "#EEF2FF", color: "#2F5FF6", padding: "2px 6px", borderRadius: "4px", flexShrink: 0 }}>AI</span>
                )}
                <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "10px", color: "#9CA3AF", flexShrink: 0 }}>{a.time}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Deep Dive Mockup ──────────────────────────────────────────────────────────

function DeepDiveMockup() {
  const [hoveredAnnotation, setHoveredAnnotation] = useState<number | null>(null);

  const annotations = [
    { x: "28%", y: "32%", label: "Anomaly Detected", desc: "4.2σ deviation from 7-day baseline" },
    { x: "64%", y: "18%", label: "Root Cause", desc: "Correlated with deploy: api-v2.3.1" },
  ];

  return (
    <div style={{
      background: "#FFFFFF", border: "1px solid #E4E5E9", borderRadius: "12px",
      boxShadow: "0 2px 16px rgba(16,17,20,0.06)",
      overflow: "hidden"
    }}>
      {/* Header */}
      <div style={{ borderBottom: "1px solid #E4E5E9", padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: "13px", fontWeight: 600, color: "#101114" }}>Service Correlation Graph</div>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "10px", color: "#6B7280", marginTop: "2px" }}>api-gateway → postgres → cache — last 6h</div>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          {["1h","6h","24h","7d"].map(t => (
            <button key={t} style={{
              fontFamily: "JetBrains Mono, monospace", fontSize: "10px",
              padding: "4px 8px", borderRadius: "5px",
              background: t === "6h" ? "#EEF2FF" : "transparent",
              color: t === "6h" ? "#2F5FF6" : "#6B7280",
              border: t === "6h" ? "1px solid #C7D2FE" : "1px solid transparent",
              cursor: "pointer"
            }}>{t}</button>
          ))}
        </div>
      </div>

      {/* Chart area */}
      <div style={{ position: "relative", padding: "16px" }}>
        <svg width="100%" viewBox="0 0 400 160" style={{ display: "block", overflow: "visible" }}>
          <defs>
            <linearGradient id="dg1" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2F5FF6" stopOpacity="0.15"/>
              <stop offset="100%" stopColor="#2F5FF6" stopOpacity="0"/>
            </linearGradient>
            <linearGradient id="dg2" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#EF4444" stopOpacity="0.12"/>
              <stop offset="100%" stopColor="#EF4444" stopOpacity="0"/>
            </linearGradient>
            <linearGradient id="dg3" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22C55E" stopOpacity="0.12"/>
              <stop offset="100%" stopColor="#22C55E" stopOpacity="0"/>
            </linearGradient>
          </defs>

          {/* Grid lines */}
          {[40, 80, 120].map(y => (
            <line key={y} x1="0" y1={y} x2="400" y2={y} stroke="#E4E5E9" strokeWidth="0.75" strokeDasharray="4 4"/>
          ))}

          {/* Baseline band */}
          <rect x="0" y="52" width="400" height="30" fill="#F0F4FF" rx="2" opacity="0.6"/>
          <text x="4" y="72" fill="#2F5FF6" fontSize="8" fontFamily="JetBrains Mono,monospace" opacity="0.7">baseline ±1σ</text>

          {/* Series 1: api-gateway latency */}
          <polygon
            points="0,80 30,78 60,76 90,82 120,58 150,32 170,22 190,30 220,45 250,55 280,62 310,68 340,72 370,75 400,77 400,155 0,155"
            fill="url(#dg1)"/>
          <polyline
            points="0,80 30,78 60,76 90,82 120,58 150,32 170,22 190,30 220,45 250,55 280,62 310,68 340,72 370,75 400,77"
            fill="none" stroke="#2F5FF6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>

          {/* Anomaly marker */}
          <circle cx="170" cy="22" r="5" fill="#EF4444" opacity="0.9"/>
          <circle cx="170" cy="22" r="10" fill="none" stroke="#EF4444" strokeWidth="1.5" opacity="0.4"/>

          {/* Series 2: postgres conn */}
          <polygon
            points="0,100 30,98 60,96 90,94 120,88 150,75 170,65 190,60 220,65 250,70 280,75 310,80 340,84 370,88 400,90 400,155 0,155"
            fill="url(#dg2)"/>
          <polyline
            points="0,100 30,98 60,96 90,94 120,88 150,75 170,65 190,60 220,65 250,70 280,75 310,80 340,84 370,88 400,90"
            fill="none" stroke="#EF4444" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="5 2"/>

          {/* Series 3: cache hit rate */}
          <polyline
            points="0,55 30,54 60,55 90,56 120,58 150,70 170,80 190,82 220,78 250,72 280,66 310,62 340,58 370,56 400,55"
            fill="none" stroke="#22C55E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>

          {/* Deploy line */}
          <line x1="255" y1="0" x2="255" y2="155" stroke="#F59E0B" strokeWidth="1" strokeDasharray="3 3" opacity="0.8"/>
          <text x="258" y="12" fill="#F59E0B" fontSize="8" fontFamily="JetBrains Mono,monospace">deploy</text>
        </svg>

        {/* Annotations */}
        {annotations.map((ann, i) => (
          <div key={i}
            onMouseEnter={() => setHoveredAnnotation(i)}
            onMouseLeave={() => setHoveredAnnotation(null)}
            style={{
              position: "absolute",
              left: ann.x, top: ann.y,
              cursor: "pointer",
            }}>
            <div style={{
              width: 20, height: 20, borderRadius: "50%",
              background: i === 0 ? "#FEF2F2" : "#EEF2FF",
              border: `1.5px solid ${i === 0 ? "#EF4444" : "#2F5FF6"}`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "10px", fontWeight: 700,
              color: i === 0 ? "#EF4444" : "#2F5FF6",
              fontFamily: "JetBrains Mono, monospace",
            }}>{i + 1}</div>
            {hoveredAnnotation === i && (
              <div style={{
                position: "absolute", bottom: "110%", left: "50%", transform: "translateX(-50%)",
                background: "#101114", color: "#fff", borderRadius: "8px",
                padding: "8px 12px", minWidth: 180, zIndex: 10,
                boxShadow: "0 4px 16px rgba(0,0,0,0.2)"
              }}>
                <div style={{ fontSize: "11px", fontWeight: 600, marginBottom: "3px" }}>{ann.label}</div>
                <div style={{ fontSize: "10px", color: "#9CA3AF", fontFamily: "JetBrains Mono, monospace" }}>{ann.desc}</div>
                <div style={{ position: "absolute", bottom: "-5px", left: "50%", transform: "translateX(-50%)", width: 10, height: 10, background: "#101114", clipPath: "polygon(0 0, 100% 0, 50% 100%)" }}/>
              </div>
            )}
          </div>
        ))}

        {/* Legend */}
        <div style={{ display: "flex", gap: "16px", marginTop: "8px" }}>
          {[
            { color: "#2F5FF6", label: "api-gateway latency", style: "solid" },
            { color: "#EF4444", label: "postgres connections", style: "dashed" },
            { color: "#22C55E", label: "cache hit rate", style: "solid" },
          ].map((l, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: "5px" }}>
              <svg width="16" height="8" viewBox="0 0 16 8">
                <line x1="0" y1="4" x2="16" y2="4" stroke={l.color} strokeWidth="1.5" strokeDasharray={l.style === "dashed" ? "4 2" : "none"} strokeLinecap="round"/>
              </svg>
              <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "9px", color: "#6B7280" }}>{l.label}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Start Monitoring Modal ───────────────────────────────────────────────────

const STACKS = [
  { id: "prometheus", label: "Prometheus", mono: "scrape_configs" },
  { id: "otel", label: "OpenTelemetry", mono: "otel-collector" },
  { id: "grafana", label: "Grafana Cloud", mono: "grafana datasource" },
  { id: "datadog", label: "Datadog Agent", mono: "dd-agent" },
  { id: "cloudwatch", label: "AWS CloudWatch", mono: "aws-sdk" },
  { id: "custom", label: "Custom / Other", mono: "webhook / API" },
];

type ModalStep = "account" | "stack" | "done";

function StartMonitoringModal({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<ModalStep>("account");
  const [email, setEmail] = useState("");
  const [org, setOrg] = useState("");
  const [emailError, setEmailError] = useState("");
  const [selectedStacks, setSelectedStacks] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  function handleAccountNext() {
    if (!email.includes("@") || !email.includes(".")) {
      setEmailError("Enter a valid work email.");
      return;
    }
    setEmailError("");
    setStep("stack");
  }

  function handleStackNext() {
    setSubmitting(true);
    setTimeout(() => { setSubmitting(false); setStep("done"); }, 1200);
  }

  function toggleStack(id: string) {
    setSelectedStacks(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const stepIndex = step === "account" ? 0 : step === "stack" ? 1 : 2;

  return (
    <div
      ref={backdropRef}
      onClick={e => { if (e.target === backdropRef.current) onClose(); }}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(16,17,20,0.48)",
        backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 16,
        animation: "fadeIn 0.15s ease"
      }}
    >
      <style>{`
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes slideUp { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        .modal-input:focus { outline: none; border-color: #2F5FF6 !important; box-shadow: 0 0 0 3px rgba(47,95,246,0.12); }
        .stack-chip:hover { border-color: #C7D2FE !important; background: #EEF2FF !important; }
      `}</style>

      <div style={{
        background: "#FFFFFF", borderRadius: 16, width: "100%", maxWidth: 480,
        boxShadow: "0 24px 80px rgba(16,17,20,0.16), 0 4px 16px rgba(16,17,20,0.08)",
        animation: "slideUp 0.2s ease",
        overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{ padding: "24px 28px 20px", borderBottom: "1px solid #E4E5E9", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 24, height: 24, borderRadius: 6, background: "#2F5FF6", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
                  <path d="M3 8a5 5 0 0 1 10 0" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
                  <circle cx="8" cy="8" r="2" fill="white"/>
                  <path d="M8 6V3M5 7.2 2.8 5M11 7.2 13.2 5" stroke="white" strokeWidth="1.25" strokeLinecap="round"/>
                </svg>
              </div>
              <span style={{ fontSize: 15, fontWeight: 700, color: "#101114", letterSpacing: "-0.02em" }}>IntelliOps</span>
            </div>
            <div style={{ fontSize: 13, color: "#6B7280", marginTop: 4 }}>
              {step === "account" && "Create your account — free for 14 days"}
              {step === "stack" && "What are you monitoring?"}
              {step === "done" && "You're all set"}
            </div>
          </div>
          <button onClick={onClose} style={{
            width: 32, height: 32, borderRadius: 8, border: "1px solid #E4E5E9",
            background: "transparent", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", color: "#6B7280"
          }}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
              <path d="M2 2l10 10M12 2 2 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </button>
        </div>

        {/* Progress bar */}
        {step !== "done" && (
          <div style={{ height: 2, background: "#E4E5E9" }}>
            <div style={{
              height: "100%", background: "#2F5FF6",
              width: `${(stepIndex + 1) / 2 * 100}%`,
              transition: "width 0.3s ease"
            }}/>
          </div>
        )}

        {/* Body */}
        <div style={{ padding: "28px 28px 24px" }}>

          {/* Step 1: Account */}
          {step === "account" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "#374151", marginBottom: 6 }}>Work email</label>
                <input
                  className="modal-input"
                  type="email"
                  autoFocus
                  placeholder="you@company.com"
                  value={email}
                  onChange={e => { setEmail(e.target.value); setEmailError(""); }}
                  onKeyDown={e => { if (e.key === "Enter") handleAccountNext(); }}
                  style={{
                    width: "100%", padding: "10px 12px", borderRadius: 8,
                    border: `1px solid ${emailError ? "#EF4444" : "#E4E5E9"}`,
                    fontSize: 14, color: "#101114", background: "#FAFAFA",
                    transition: "border-color 0.15s, box-shadow 0.15s",
                    fontFamily: "inherit",
                  }}
                />
                {emailError && <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#EF4444", marginTop: 5 }}>{emailError}</div>}
              </div>
              <div>
                <label style={{ display: "block", fontSize: 13, fontWeight: 600, color: "#374151", marginBottom: 6 }}>Organization name</label>
                <input
                  className="modal-input"
                  type="text"
                  placeholder="Acme Corp"
                  value={org}
                  onChange={e => setOrg(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") handleAccountNext(); }}
                  style={{
                    width: "100%", padding: "10px 12px", borderRadius: 8,
                    border: "1px solid #E4E5E9", fontSize: 14, color: "#101114",
                    background: "#FAFAFA", transition: "border-color 0.15s, box-shadow 0.15s",
                    fontFamily: "inherit",
                  }}
                />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 12px", background: "#F0FDF4", borderRadius: 8, border: "1px solid #BBF7D0" }}>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2.5 7l3 3 6-6" stroke="#16A34A" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                <span style={{ fontSize: 12, color: "#15803D" }}>No credit card required · SOC 2 Type II certified</span>
              </div>
              <button
                onClick={handleAccountNext}
                style={{
                  width: "100%", padding: "12px", borderRadius: 10,
                  background: "#2F5FF6", color: "#FFFFFF",
                  fontSize: 14, fontWeight: 600, border: "none", cursor: "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  transition: "background 0.15s",
                }}
                onMouseEnter={e => (e.currentTarget.style.background = "#1E4DE0")}
                onMouseLeave={e => (e.currentTarget.style.background = "#2F5FF6")}
              >
                Continue <IconArrowRight/>
              </button>
              <div style={{ textAlign: "center" }}>
                <span style={{ fontSize: 13, color: "#6B7280" }}>Already have an account? </span>
                <a href="#" style={{ fontSize: 13, color: "#2F5FF6", fontWeight: 500, textDecoration: "none" }}>Sign in</a>
              </div>
            </div>
          )}

          {/* Step 2: Stack selection */}
          {step === "stack" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              <div>
                <div style={{ fontSize: 13, color: "#6B7280", marginBottom: 14 }}>
                  Select the datasources you want to connect. You can add more later.
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  {STACKS.map(s => {
                    const active = selectedStacks.has(s.id);
                    return (
                      <button
                        key={s.id}
                        className="stack-chip"
                        onClick={() => toggleStack(s.id)}
                        style={{
                          padding: "10px 12px", borderRadius: 9, cursor: "pointer",
                          border: `1px solid ${active ? "#2F5FF6" : "#E4E5E9"}`,
                          background: active ? "#EEF2FF" : "#FAFAFA",
                          textAlign: "left", transition: "all 0.15s",
                        }}
                      >
                        <div style={{ fontSize: 13, fontWeight: 600, color: active ? "#2F5FF6" : "#101114", marginBottom: 2 }}>{s.label}</div>
                        <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: active ? "#6366F1" : "#9CA3AF" }}>{s.mono}</div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <button
                onClick={handleStackNext}
                disabled={submitting}
                style={{
                  width: "100%", padding: "12px", borderRadius: 10,
                  background: selectedStacks.size === 0 ? "#E4E5E9" : "#2F5FF6",
                  color: selectedStacks.size === 0 ? "#9CA3AF" : "#FFFFFF",
                  fontSize: 14, fontWeight: 600, border: "none",
                  cursor: selectedStacks.size === 0 ? "not-allowed" : "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  transition: "background 0.15s",
                }}
                onMouseEnter={e => { if (selectedStacks.size > 0 && !submitting) (e.currentTarget.style.background = "#1E4DE0"); }}
                onMouseLeave={e => { if (selectedStacks.size > 0 && !submitting) (e.currentTarget.style.background = "#2F5FF6"); }}
              >
                {submitting ? (
                  <>
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ animation: "spin 0.8s linear infinite" }}>
                      <style>{"@keyframes spin { to { transform: rotate(360deg); } }"}</style>
                      <circle cx="7" cy="7" r="5" stroke="rgba(255,255,255,0.4)" strokeWidth="2"/>
                      <path d="M7 2a5 5 0 0 1 5 5" stroke="white" strokeWidth="2" strokeLinecap="round"/>
                    </svg>
                    Setting up workspace…
                  </>
                ) : (
                  <>Launch IntelliOps <IconArrowRight/></>
                )}
              </button>

              <button onClick={() => setStep("account")} style={{
                background: "none", border: "none", cursor: "pointer",
                fontSize: 13, color: "#6B7280", textAlign: "center"
              }}>← Back</button>
            </div>
          )}

          {/* Step 3: Done */}
          {step === "done" && (
            <div style={{ textAlign: "center", padding: "8px 0 4px" }}>
              <div style={{
                width: 56, height: 56, borderRadius: "50%", background: "#F0FDF4",
                border: "1px solid #BBF7D0", margin: "0 auto 20px",
                display: "flex", alignItems: "center", justifyContent: "center"
              }}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                  <path d="M5 12l5 5 9-9" stroke="#16A34A" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>
              <h3 style={{ fontSize: 20, fontWeight: 800, color: "#101114", letterSpacing: "-0.02em", margin: "0 0 8px" }}>
                Workspace created
              </h3>
              <p style={{ fontSize: 14, color: "#6B7280", lineHeight: 1.6, margin: "0 0 8px" }}>
                We sent a confirmation to <strong style={{ color: "#101114" }}>{email || "your email"}</strong>.<br/>
                Your 14-day trial starts now.
              </p>
              <div style={{
                margin: "20px 0 24px", background: "#F7F8FA", border: "1px solid #E4E5E9",
                borderRadius: 10, padding: "14px 16px", textAlign: "left"
              }}>
                <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#6B7280", marginBottom: 8 }}>NEXT STEP</div>
                <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#101114", lineHeight: 1.7 }}>
                  <span style={{ color: "#9CA3AF" }}>$</span> curl -sSL https://intelliops.app/install | sh<br/>
                  <span style={{ color: "#9CA3AF" }}>$</span> intelliops init --token <span style={{ color: "#2F5FF6" }}>IO_XXXX_XXXX</span>
                </div>
              </div>
              <button
                onClick={onClose}
                style={{
                  width: "100%", padding: "12px", borderRadius: 10,
                  background: "#2F5FF6", color: "#FFFFFF",
                  fontSize: 14, fontWeight: 600, border: "none", cursor: "pointer",
                  transition: "background 0.15s",
                }}
                onMouseEnter={e => (e.currentTarget.style.background = "#1E4DE0")}
                onMouseLeave={e => (e.currentTarget.style.background = "#2F5FF6")}
              >
                Open Dashboard
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Nav ───────────────────────────────────────────────────────────────────────

function Nav({ page, onNavigate }: { page: Page; onNavigate: (p: Page) => void }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 8);
    window.addEventListener("scroll", fn);
    return () => window.removeEventListener("scroll", fn);
  }, []);

  const navLinks: { label: string; page?: Page; href?: string }[] = [
    { label: "Product",   page: "product"   },
    { label: "Audit Log", page: "audit-log" },
    { label: "Docs",      page: "docs"      },
  ];

  return (
    <nav style={{
      position: "fixed", top: 0, left: 0, right: 0, zIndex: 100,
      background: scrolled || page !== "landing" ? "rgba(247,248,250,0.95)" : "transparent",
      backdropFilter: scrolled || page !== "landing" ? "blur(12px)" : "none",
      borderBottom: scrolled || page !== "landing" ? "1px solid #E4E5E9" : "1px solid transparent",
      transition: "all 0.2s ease",
    }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px", height: 60, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        {/* Logo */}
        <button onClick={() => onNavigate("landing")} style={{
          display: "flex", alignItems: "center", gap: 8, background: "none", border: "none", cursor: "pointer", padding: 0,
        }}>
          <div style={{ width: 28, height: 28, borderRadius: "7px", background: "#2F5FF6", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M3 8a5 5 0 0 1 10 0" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
              <circle cx="8" cy="8" r="2" fill="white"/>
              <path d="M8 6V3M5 7.2 2.8 5M11 7.2 13.2 5" stroke="white" strokeWidth="1.25" strokeLinecap="round"/>
            </svg>
          </div>
          <span style={{ fontSize: "15px", fontWeight: 700, color: "#101114", letterSpacing: "-0.02em" }}>IntelliOps</span>
        </button>

        {/* Links */}
        <div style={{ display: "flex", alignItems: "center", gap: "2px" }}>
          {navLinks.map(link => {
            const active = link.page ? page === link.page : false;
            return (
              <button key={link.label}
                onClick={() => link.page ? onNavigate(link.page) : undefined}
                style={{
                  padding: "6px 12px", borderRadius: "6px",
                  fontSize: "14px", fontWeight: active ? 600 : 500,
                  color: active ? "#2F5FF6" : "#6B7280",
                  background: active ? "#EEF2FF" : "transparent",
                  border: "none", cursor: "pointer", textDecoration: "none",
                  transition: "color 0.15s",
                }}
                onMouseEnter={e => { if (!active) (e.currentTarget.style.color = "#101114"); }}
                onMouseLeave={e => { if (!active) (e.currentTarget.style.color = "#6B7280"); }}
              >{link.label}</button>
            );
          })}
        </div>

        {/* CTA */}
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button onClick={() => onNavigate("dashboard")} style={{
            fontSize: "14px", fontWeight: 500, color: "#6B7280",
            background: "none", border: "none", cursor: "pointer", padding: "6px 12px",
            transition: "color 0.15s",
          }}
          onMouseEnter={e => (e.currentTarget.style.color = "#101114")}
          onMouseLeave={e => (e.currentTarget.style.color = "#6B7280")}
          >Sign in</button>
          <button onClick={() => onNavigate("dashboard")} style={{
            fontSize: "14px", fontWeight: 600, color: "#FFFFFF",
            background: "#2F5FF6", padding: "7px 16px", borderRadius: "8px",
            border: "none", cursor: "pointer", transition: "background 0.15s",
          }}
          onMouseEnter={e => (e.currentTarget.style.background = "#1E4DE0")}
          onMouseLeave={e => (e.currentTarget.style.background = "#2F5FF6")}
          >Get Started</button>
        </div>
      </div>
    </nav>
  );
}

// ─── Hero ──────────────────────────────────────────────────────────────────────

function Hero({ onStartMonitoring, onViewDemo }: { onStartMonitoring: () => void; onViewDemo: () => void }) {
  return (
    <section style={{ paddingTop: 140, paddingBottom: 100, background: "#F7F8FA", overflow: "hidden", position: "relative" }}>
      {/* Subtle grid bg */}
      <div style={{
        position: "absolute", inset: 0, opacity: 0.4,
        backgroundImage: "linear-gradient(#E4E5E9 1px, transparent 1px), linear-gradient(90deg, #E4E5E9 1px, transparent 1px)",
        backgroundSize: "48px 48px",
        maskImage: "radial-gradient(ellipse 80% 60% at 50% 0%, black 40%, transparent 100%)"
      }}/>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px", position: "relative" }}>
        {/* Eyebrow */}
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 24 }}>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            background: "#EEF2FF", border: "1px solid #C7D2FE",
            borderRadius: "100px", padding: "5px 14px"
          }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#2F5FF6" }}/>
            <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#2F5FF6", fontWeight: 500 }}>
              AIOps · Powered by Prometheus + Grafana
            </span>
          </div>
        </div>

        {/* Headline */}
        <h1 style={{
          textAlign: "center",
          fontSize: "clamp(40px, 6vw, 68px)",
          fontWeight: 800,
          color: "#101114",
          letterSpacing: "-0.04em",
          lineHeight: 1.08,
          maxWidth: 800,
          margin: "0 auto 20px",
        }}>
          Cut through the noise.<br/>
          <span style={{ color: "#2F5FF6" }}>AI finds the root cause.</span>
        </h1>

        {/* Subheadline */}
        <p style={{
          textAlign: "center", fontSize: 18, color: "#6B7280",
          maxWidth: 560, margin: "0 auto 40px",
          lineHeight: 1.6, fontWeight: 400
        }}>
          IntelliOps monitors your cloud infrastructure, correlates signals across services, and delivers precise, actionable alerts — not a wall of noise.
        </p>

        {/* CTAs */}
        <div style={{ display: "flex", justifyContent: "center", gap: 12, marginBottom: 72 }}>
          <button onClick={onStartMonitoring} style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            fontSize: 15, fontWeight: 600, color: "#FFFFFF",
            background: "#2F5FF6", padding: "12px 24px", borderRadius: "10px",
            border: "none", cursor: "pointer", transition: "all 0.15s",
            boxShadow: "0 2px 8px rgba(47,95,246,0.3)"
          }}
          onMouseEnter={e => (e.currentTarget.style.background = "#1E4DE0")}
          onMouseLeave={e => (e.currentTarget.style.background = "#2F5FF6")}
          >
            Start Monitoring <IconArrowRight/>
          </button>
          <button onClick={onViewDemo} style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            fontSize: 15, fontWeight: 600, color: "#101114",
            background: "#FFFFFF", padding: "12px 24px", borderRadius: "10px",
            border: "1px solid #E4E5E9", cursor: "pointer",
            transition: "all 0.15s"
          }}
          onMouseEnter={e => (e.currentTarget.style.borderColor = "#C7D2FE")}
          onMouseLeave={e => (e.currentTarget.style.borderColor = "#E4E5E9")}
          >
            View Demo
          </button>
        </div>

        {/* Dashboard mockup */}
        <HeroDashboard/>
      </div>
    </section>
  );
}

// ─── Trust Bar ─────────────────────────────────────────────────────────────────

function TrustBar() {
  const techs = [
    { name: "Prometheus", logo: "P" },
    { name: "Grafana", logo: "G" },
    { name: "Go", logo: "Go" },
    { name: "AWS", logo: "AWS" },
    { name: "Kubernetes", logo: "K8s" },
    { name: "OpenTelemetry", logo: "OTel" },
  ];
  return (
    <section style={{ background: "#FFFFFF", borderTop: "1px solid #E4E5E9", borderBottom: "1px solid #E4E5E9", padding: "24px 0" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 40, justifyContent: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, fontWeight: 500, color: "#9CA3AF", letterSpacing: "0.06em", textTransform: "uppercase", whiteSpace: "nowrap" }}>Built on</span>
          {techs.map(t => (
            <div key={t.name} style={{ display: "flex", alignItems: "center", gap: 7, opacity: 0.65 }}
              onMouseEnter={e => (e.currentTarget.style.opacity = "1")}
              onMouseLeave={e => (e.currentTarget.style.opacity = "0.65")}
            >
              <div style={{
                width: 28, height: 28, borderRadius: "7px",
                background: "#EDEEF1", display: "flex", alignItems: "center", justifyContent: "center",
                fontFamily: "JetBrains Mono, monospace", fontSize: 9, fontWeight: 700, color: "#6B7280"
              }}>{t.logo}</div>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#6B7280" }}>{t.name}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ─── Features ─────────────────────────────────────────────────────────────────

function Features() {
  const features = [
    {
      icon: <IconBrain/>,
      title: "AI Anomaly Detection",
      desc: "Learns your infrastructure's baseline behavior over 7 days. Flags statistically significant deviations before they become incidents — with zero manual threshold tuning.",
      tag: "ML-powered",
    },
    {
      icon: <IconBell/>,
      title: "Smart Alerting",
      desc: "Native Prometheus integration with AI deduplication and grouping. Cut alert fatigue by up to 40% by routing only high-signal, correlated events to your on-call team.",
      tag: "Prometheus",
    },
    {
      icon: <IconSearch/>,
      title: "Root-Cause Analysis",
      desc: "Automatically correlates signals across services, traces, logs, and deploys. Surface the precise chain of events that caused an incident — in seconds, not hours.",
      tag: "AI Insights",
    },
    {
      icon: <IconChart/>,
      title: "Unified Observability",
      desc: "Metrics, logs, traces, and alerts in one coherent workspace. Built-in Grafana-compatible dashboards mean zero migration cost from your existing stack.",
      tag: "Grafana-native",
    },
  ];

  return (
    <section style={{ background: "#F7F8FA", padding: "100px 0" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px" }}>
        <div style={{ marginBottom: 56, maxWidth: 520 }}>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#2F5FF6", fontWeight: 500, marginBottom: 12, letterSpacing: "0.04em" }}>
            CAPABILITIES
          </div>
          <h2 style={{ fontSize: "clamp(28px, 4vw, 40px)", fontWeight: 800, color: "#101114", letterSpacing: "-0.03em", lineHeight: 1.15, margin: "0 0 16px" }}>
            Observability that thinks ahead
          </h2>
          <p style={{ fontSize: 16, color: "#6B7280", lineHeight: 1.6, margin: 0 }}>
            Built for SRE teams who need signal clarity, not more dashboards to stare at.
          </p>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 20 }}>
          {features.map((f, i) => (
            <div key={i} style={{
              background: "#FFFFFF", border: "1px solid #E4E5E9",
              borderRadius: 12, padding: 28,
              boxShadow: "0 1px 4px rgba(16,17,20,0.04)",
              transition: "box-shadow 0.2s, border-color 0.2s",
              cursor: "default"
            }}
            onMouseEnter={e => {
              (e.currentTarget as HTMLElement).style.boxShadow = "0 4px 20px rgba(47,95,246,0.1)";
              (e.currentTarget as HTMLElement).style.borderColor = "#C7D2FE";
            }}
            onMouseLeave={e => {
              (e.currentTarget as HTMLElement).style.boxShadow = "0 1px 4px rgba(16,17,20,0.04)";
              (e.currentTarget as HTMLElement).style.borderColor = "#E4E5E9";
            }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20 }}>
                <div style={{
                  width: 40, height: 40, borderRadius: 10,
                  background: "#EEF2FF", color: "#2F5FF6",
                  display: "flex", alignItems: "center", justifyContent: "center"
                }}>
                  {f.icon}
                </div>
                <span style={{
                  fontFamily: "JetBrains Mono, monospace", fontSize: 10, fontWeight: 500,
                  color: "#2F5FF6", background: "#EEF2FF", padding: "3px 8px", borderRadius: 5
                }}>{f.tag}</span>
              </div>
              <h3 style={{ fontSize: 16, fontWeight: 700, color: "#101114", margin: "0 0 10px", letterSpacing: "-0.01em" }}>{f.title}</h3>
              <p style={{ fontSize: 14, color: "#6B7280", lineHeight: 1.65, margin: 0 }}>{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ─── Deep Dive ─────────────────────────────────────────────────────────────────

function DeepDive() {
  return (
    <section style={{ background: "#EDEEF1", padding: "100px 0" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 64, alignItems: "center" }}>
          {/* Left text */}
          <div>
            <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#2F5FF6", fontWeight: 500, marginBottom: 12, letterSpacing: "0.04em" }}>
              SIGNAL CORRELATION
            </div>
            <h2 style={{ fontSize: "clamp(26px, 3.5vw, 38px)", fontWeight: 800, color: "#101114", letterSpacing: "-0.03em", lineHeight: 1.15, margin: "0 0 20px" }}>
              The AI that reads your entire stack simultaneously
            </h2>
            <p style={{ fontSize: 16, color: "#6B7280", lineHeight: 1.65, margin: "0 0 28px" }}>
              Most monitoring tools show you metrics in isolation. IntelliOps correlates latency spikes, connection pool pressure, cache miss rates, and deployment events across all services — in real time.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {[
                "Detects correlated failure patterns across service boundaries",
                "Enriches alerts with causal chains, not just symptom data",
                "Reduces mean time to root cause (MTTRC) by 65%",
                "Integrates with PagerDuty, Slack, and OpsGenie out of the box",
              ].map((point, i) => (
                <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                  <div style={{
                    width: 18, height: 18, borderRadius: "50%", background: "#EEF2FF", color: "#2F5FF6",
                    display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 1
                  }}>
                    <IconCheck/>
                  </div>
                  <span style={{ fontSize: 14, color: "#374151", lineHeight: 1.55 }}>{point}</span>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 36 }}>
              <a href="#" style={{
                display: "inline-flex", alignItems: "center", gap: 8,
                fontSize: 14, fontWeight: 600, color: "#2F5FF6", textDecoration: "none",
              }}
              onMouseEnter={e => (e.currentTarget.style.gap = "12px")}
              onMouseLeave={e => (e.currentTarget.style.gap = "8px")}
              >
                Read the technical docs <IconArrowRight/>
              </a>
            </div>
          </div>

          {/* Right mockup */}
          <div>
            <DeepDiveMockup/>
          </div>
        </div>
      </div>
    </section>
  );
}

// ─── Stats Strip ──────────────────────────────────────────────────────────────

function StatsStrip() {
  const stats = [
    { value: "40%", label: "fewer false alerts", sub: "across 500+ production environments" },
    { value: "<1s", label: "detection latency", sub: "from metric ingestion to alert" },
    { value: "65%", label: "faster MTTRC", sub: "vs manual investigation" },
    { value: "99.95%", label: "platform uptime", sub: "30-day rolling average" },
  ];

  return (
    <section style={{ background: "#FFFFFF", padding: "80px 0", borderTop: "1px solid #E4E5E9", borderBottom: "1px solid #E4E5E9" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
          {stats.map((s, i) => (
            <div key={i} style={{
              textAlign: "center", padding: "32px 20px",
              borderRight: i < 3 ? "1px solid #E4E5E9" : "none"
            }}>
              <div style={{
                fontFamily: "JetBrains Mono, monospace",
                fontSize: "clamp(32px, 4vw, 48px)",
                fontWeight: 700, color: "#101114",
                letterSpacing: "-0.03em", lineHeight: 1,
                marginBottom: 8
              }}>{s.value}</div>
              <div style={{ fontSize: 15, fontWeight: 600, color: "#101114", marginBottom: 4 }}>{s.label}</div>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#9CA3AF" }}>{s.sub}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ─── How It Works ─────────────────────────────────────────────────────────────


function HowItWorks() {
  const steps = [
    {
      num: "01",
      icon: <IconConnector/>,
      title: "Connect your stack",
      desc: "Drop in our Prometheus exporter, OpenTelemetry collector, or point IntelliOps at your existing Grafana datasources. Zero config changes to your infrastructure.",
      detail: "prometheus.yml · otel-collector · grafana datasource API",
    },
    {
      num: "02",
      icon: <IconBrain/>,
      title: "AI learns baseline behavior",
      desc: "Over 7 days, the anomaly engine builds a statistical model of your system's normal behavior — accounting for time-of-day, day-of-week, and release cycles.",
      detail: "7-day warm-up · rolling window · deploy-aware",
    },
    {
      num: "03",
      icon: <IconBell/>,
      title: "Get precise, actionable alerts",
      desc: "When something deviates, you get a single, high-confidence alert with a root-cause summary — not 40 pages firing at once. Route to Slack, PagerDuty, or any webhook.",
      detail: "PagerDuty · Slack · OpsGenie · custom webhook",
    },
  ];

  return (
    <section style={{ background: "#F7F8FA", padding: "100px 0" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "0 32px" }}>
        <div style={{ textAlign: "center", marginBottom: 64 }}>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 12, color: "#2F5FF6", fontWeight: 500, marginBottom: 12, letterSpacing: "0.04em" }}>
            HOW IT WORKS
          </div>
          <h2 style={{ fontSize: "clamp(28px, 4vw, 40px)", fontWeight: 800, color: "#101114", letterSpacing: "-0.03em", lineHeight: 1.15, margin: 0 }}>
            Up and running in 15 minutes
          </h2>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 0, position: "relative" }}>
          {/* Connector line */}
          <div style={{
            position: "absolute", top: 36, left: "calc(33.33% / 2)", right: "calc(33.33% / 2)",
            height: 1, background: "linear-gradient(90deg, #C7D2FE, #E4E5E9 50%, #C7D2FE)",
            zIndex: 0
          }}/>

          {steps.map((step, i) => (
            <div key={i} style={{ padding: "0 32px", position: "relative", zIndex: 1 }}>
              <div style={{
                width: 72, height: 72, borderRadius: "50%",
                background: "#FFFFFF", border: "1px solid #E4E5E9",
                boxShadow: "0 2px 8px rgba(16,17,20,0.06)",
                display: "flex", alignItems: "center", justifyContent: "center",
                marginBottom: 24, position: "relative"
              }}>
                <div style={{ color: "#2F5FF6" }}>{step.icon}</div>
                <div style={{
                  position: "absolute", top: -4, right: -4,
                  background: "#2F5FF6", color: "#fff",
                  fontFamily: "JetBrains Mono, monospace", fontSize: 9, fontWeight: 700,
                  width: 22, height: 22, borderRadius: "50%",
                  display: "flex", alignItems: "center", justifyContent: "center"
                }}>{step.num}</div>
              </div>
              <h3 style={{ fontSize: 18, fontWeight: 700, color: "#101114", margin: "0 0 12px", letterSpacing: "-0.01em" }}>{step.title}</h3>
              <p style={{ fontSize: 14, color: "#6B7280", lineHeight: 1.65, margin: "0 0 16px" }}>{step.desc}</p>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, color: "#9CA3AF", lineHeight: 1.8 }}>{step.detail}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ─── CTA Banner ───────────────────────────────────────────────────────────────

function CTABanner({ onStartMonitoring }: { onStartMonitoring: () => void }) {
  return (
    <section style={{
      background: "linear-gradient(135deg, #1A3AD8 0%, #2F5FF6 50%, #4353FF 100%)",
      padding: "96px 32px",
    }}>
      <div style={{ maxWidth: 640, margin: "0 auto", textAlign: "center" }}>
        <div style={{
          fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "rgba(255,255,255,0.6)",
          marginBottom: 16, letterSpacing: "0.08em"
        }}>GET STARTED TODAY</div>
        <h2 style={{
          fontSize: "clamp(28px, 4vw, 44px)", fontWeight: 800, color: "#FFFFFF",
          letterSpacing: "-0.03em", lineHeight: 1.12, margin: "0 0 16px"
        }}>
          Your on-call team deserves better than alert fatigue
        </h2>
        <p style={{ fontSize: 16, color: "rgba(255,255,255,0.72)", lineHeight: 1.6, margin: "0 0 40px" }}>
          Connect your first datasource in minutes. No credit card required for the 14-day trial.
        </p>
        <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
          <button onClick={onStartMonitoring} style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            fontSize: 15, fontWeight: 700, color: "#2F5FF6",
            background: "#FFFFFF", padding: "13px 28px", borderRadius: 10,
            border: "none", cursor: "pointer", transition: "transform 0.15s",
            boxShadow: "0 2px 12px rgba(0,0,0,0.15)"
          }}
          onMouseEnter={e => (e.currentTarget.style.transform = "translateY(-1px)")}
          onMouseLeave={e => (e.currentTarget.style.transform = "none")}
          >
            Start Free Trial <IconArrowRight/>
          </button>
          <a href="#" style={{
            display: "inline-flex", alignItems: "center", gap: 8,
            fontSize: 15, fontWeight: 600, color: "rgba(255,255,255,0.9)",
            padding: "13px 28px", borderRadius: 10,
            textDecoration: "none", border: "1px solid rgba(255,255,255,0.3)",
            transition: "border-color 0.15s"
          }}
          onMouseEnter={e => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.6)")}
          onMouseLeave={e => (e.currentTarget.style.borderColor = "rgba(255,255,255,0.3)")}
          >
            Talk to an engineer
          </a>
        </div>
        <div style={{ marginTop: 28, display: "flex", justifyContent: "center", gap: 24 }}>
          {["No credit card", "SOC 2 Type II", "99.95% SLA"].map(b => (
            <div key={b} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ color: "rgba(255,255,255,0.6)", display: "flex" }}><IconCheck/></div>
              <span style={{ fontSize: 13, color: "rgba(255,255,255,0.6)" }}>{b}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ─── Footer ───────────────────────────────────────────────────────────────────

function Footer() {
  const cols = [
    {
      heading: "Product",
      links: ["Features", "Integrations", "Changelog", "Roadmap", "Status"],
    },
    {
      heading: "Resources",
      links: ["Documentation", "API Reference", "Prometheus Guide", "SRE Handbook", "Blog"],
    },
    {
      heading: "Company",
      links: ["About", "Careers", "Security", "Privacy Policy", "Terms of Service"],
    },
  ];

  return (
    <footer style={{ background: "#FFFFFF", borderTop: "1px solid #E4E5E9", padding: "64px 32px 40px" }}>
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>
        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr 1fr", gap: 48, marginBottom: 56 }}>
          {/* Brand column */}
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
              <div style={{
                width: 28, height: 28, borderRadius: "7px", background: "#2F5FF6",
                display: "flex", alignItems: "center", justifyContent: "center"
              }}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path d="M3 8a5 5 0 0 1 10 0" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
                  <circle cx="8" cy="8" r="2" fill="white"/>
                  <path d="M8 6V3M5 7.2 2.8 5M11 7.2 13.2 5" stroke="white" strokeWidth="1.25" strokeLinecap="round"/>
                </svg>
              </div>
              <span style={{ fontSize: 15, fontWeight: 700, color: "#101114", letterSpacing: "-0.02em" }}>IntelliOps</span>
            </div>
            <p style={{ fontSize: 13, color: "#6B7280", lineHeight: 1.65, maxWidth: 240, margin: "0 0 20px" }}>
              AI-powered observability for engineering teams who refuse to tolerate alert fatigue.
            </p>
            <div style={{ display: "flex", gap: 10 }}>
              {[<IconGitHub/>, <IconTwitter/>, <IconLinkedIn/>].map((icon, i) => (
                <a key={i} href="#" style={{
                  width: 32, height: 32, borderRadius: 8,
                  background: "#F7F8FA", border: "1px solid #E4E5E9",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "#6B7280", textDecoration: "none", transition: "color 0.15s, border-color 0.15s"
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = "#101114"; (e.currentTarget as HTMLElement).style.borderColor = "#C7D2FE"; }}
                onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = "#6B7280"; (e.currentTarget as HTMLElement).style.borderColor = "#E4E5E9"; }}
                >{icon}</a>
              ))}
            </div>
          </div>

          {/* Link columns */}
          {cols.map(col => (
            <div key={col.heading}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#101114", marginBottom: 16, letterSpacing: "0.02em" }}>{col.heading}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {col.links.map(link => (
                  <a key={link} href="#" style={{
                    fontSize: 13, color: "#6B7280", textDecoration: "none",
                    transition: "color 0.15s"
                  }}
                  onMouseEnter={e => (e.currentTarget.style.color = "#101114")}
                  onMouseLeave={e => (e.currentTarget.style.color = "#6B7280")}
                  >{link}</a>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Bottom bar */}
        <div style={{ borderTop: "1px solid #E4E5E9", paddingTop: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#9CA3AF" }}>
            © 2024 IntelliOps, Inc. — Built with Prometheus, Go, and unreasonable standards.
          </span>
          <div style={{ display: "flex", gap: 20 }}>
            {["Privacy", "Terms", "Security", "Cookies"].map(l => (
              <a key={l} href="#" style={{ fontSize: 12, color: "#9CA3AF", textDecoration: "none" }}
              onMouseEnter={e => (e.currentTarget.style.color = "#6B7280")}
              onMouseLeave={e => (e.currentTarget.style.color = "#9CA3AF")}
              >{l}</a>
            ))}
          </div>
        </div>
      </div>
    </footer>
  );
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [page, setPage] = useState<Page>("landing");
  const [monitoringModalOpen, setMonitoringModalOpen] = useState(false);

  const openMonitoringModal = useCallback(() => setMonitoringModalOpen(true), []);
  const closeMonitoringModal = useCallback(() => setMonitoringModalOpen(false), []);

  const navigate = useCallback((p: Page) => {
    setPage(p);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  // Dashboard has its own full-screen shell — no shared Nav
  if (page === "dashboard") {
    return <Dashboard onNavigate={navigate}/>;
  }

  return (
    <div style={{ minHeight: "100vh", background: "#F7F8FA" }}>
      <Nav page={page} onNavigate={navigate}/>

      {page === "landing" && (
        <>
          <Hero onStartMonitoring={openMonitoringModal} onViewDemo={() => navigate("dashboard")}/>
          <TrustBar/>
          <Features/>
          <DeepDive/>
          <StatsStrip/>
          <HowItWorks/>
          <CTABanner onStartMonitoring={openMonitoringModal}/>
          <Footer/>
        </>
      )}

      {page === "audit-log" && <AuditLog/>}
      {page === "product"   && <Product onNavigate={navigate}/>}
      {page === "docs"      && <Docs onNavigate={navigate}/>}

      {monitoringModalOpen && <StartMonitoringModal onClose={closeMonitoringModal}/>}
    </div>
  );
}
