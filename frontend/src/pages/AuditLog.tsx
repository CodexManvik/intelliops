import { useState, useMemo } from "react";
import { AUDIT_ENTRIES } from "../data/incidents";
import type { AuditActionType, AuditStatus, AuditEntry } from "../data/incidents";

// ─── Badges ───────────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<AuditStatus, { bg: string; text: string; label: string }> = {
  success: { bg: "#F0FDF4", text: "#16A34A", label: "success" },
  failed:  { bg: "#FEF2F2", text: "#DC2626", label: "failed"  },
  pending: { bg: "#FFFBEB", text: "#D97706", label: "pending" },
};

const TYPE_STYLE: Record<AuditActionType, { bg: string; text: string; label: string }> = {
  alert:       { bg: "#FEF2F2", text: "#DC2626", label: "Alert"       },
  config:      { bg: "#EEF2FF", text: "#4338CA", label: "Config"      },
  login:       { bg: "#F0FDF4", text: "#16A34A", label: "Login"       },
  integration: { bg: "#FDF4FF", text: "#9333EA", label: "Integration" },
};

const ACTOR_TYPE_COLOR: Record<string, { dot: string; label: string }> = {
  user:   { dot: "#2F5FF6", label: "user"   },
  system: { dot: "#6B7280", label: "system" },
  api:    { dot: "#9333EA", label: "api"    },
};

// ─── Filter chip ──────────────────────────────────────────────────────────────

function Chip({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      padding: "5px 12px", borderRadius: 7,
      fontSize: 13, fontWeight: active ? 600 : 500,
      background: active ? "#2F5FF6" : "#F3F4F6",
      color: active ? "#FFFFFF" : "#6B7280",
      border: active ? "1px solid #2F5FF6" : "1px solid #E4E5E9",
      cursor: "pointer", transition: "all 0.15s",
      whiteSpace: "nowrap",
    }}>{label}</button>
  );
}

// ─── AuditLog page ────────────────────────────────────────────────────────────

export default function AuditLog() {
  const [search, setSearch] = useState("");
  const [actionType, setActionType] = useState<"all" | AuditActionType>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | AuditStatus>("all");
  const [dateRange, setDateRange] = useState<"today" | "7d" | "30d">("today");

  const filtered = useMemo(() => {
    return AUDIT_ENTRIES.filter(e => {
      if (actionType !== "all" && e.actionType !== actionType) return false;
      if (statusFilter !== "all" && e.status !== statusFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        return (
          e.actor.toLowerCase().includes(q) ||
          e.action.toLowerCase().includes(q) ||
          e.target.toLowerCase().includes(q) ||
          (e.detail?.toLowerCase().includes(q) ?? false)
        );
      }
      return true;
    });
  }, [search, actionType, statusFilter, dateRange]);

  return (
    <div style={{ minHeight: "100vh", background: "#F7F8FA", paddingTop: 60 }}>
      {/* Page header */}
      <div style={{
        background: "#FFFFFF", borderBottom: "1px solid #E4E5E9",
        padding: "28px 40px 24px",
      }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
            <div>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#9CA3AF", marginBottom: 6, letterSpacing: "0.05em" }}>
                SYSTEM / AUDIT LOG
              </div>
              <h1 style={{ fontSize: 22, fontWeight: 800, color: "#101114", margin: 0, letterSpacing: "-0.03em" }}>
                Audit Log
              </h1>
              <p style={{ fontSize: 13, color: "#6B7280", margin: "4px 0 0" }}>
                Full record of all system, user, and API actions across your IntelliOps workspace.
              </p>
            </div>
            <button style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "8px 14px", borderRadius: 8, border: "1px solid #E4E5E9",
              background: "#FFFFFF", color: "#374151", fontSize: 13, fontWeight: 500,
              cursor: "pointer", transition: "border-color 0.15s",
            }}
            onMouseEnter={e => (e.currentTarget.style.borderColor = "#C7D2FE")}
            onMouseLeave={e => (e.currentTarget.style.borderColor = "#E4E5E9")}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M7 2l5 5-5 5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              Export CSV
            </button>
          </div>
        </div>
      </div>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 40px" }}>
        {/* Filter bar */}
        <div style={{
          background: "#FFFFFF", border: "1px solid #E4E5E9", borderRadius: 12,
          padding: "14px 16px", marginBottom: 16,
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        }}>
          {/* Search */}
          <div style={{ position: "relative", flexShrink: 0 }}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"
              style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#9CA3AF" }}>
              <circle cx="6" cy="6" r="4" stroke="currentColor" strokeWidth="1.25"/>
              <path d="m9.5 9.5 2.5 2.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/>
            </svg>
            <input
              type="text"
              placeholder="Search actions, actors, targets…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                paddingLeft: 30, paddingRight: 12, paddingTop: 7, paddingBottom: 7,
                border: "1px solid #E4E5E9", borderRadius: 7, fontSize: 13,
                color: "#101114", background: "#F7F8FA", width: 240,
                fontFamily: "inherit", outline: "none",
                transition: "border-color 0.15s",
              }}
              onFocus={e => (e.currentTarget.style.borderColor = "#2F5FF6")}
              onBlur={e => (e.currentTarget.style.borderColor = "#E4E5E9")}
            />
          </div>

          {/* Divider */}
          <div style={{ width: 1, height: 24, background: "#E4E5E9", flexShrink: 0 }} />

          {/* Action type chips */}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(["all", "alert", "config", "login", "integration"] as const).map(t => (
              <Chip key={t} active={actionType === t}
                label={t === "all" ? "All types" : TYPE_STYLE[t as AuditActionType]?.label ?? t}
                onClick={() => setActionType(t)}
              />
            ))}
          </div>

          {/* Divider */}
          <div style={{ width: 1, height: 24, background: "#E4E5E9", flexShrink: 0 }} />

          {/* Status chips */}
          <div style={{ display: "flex", gap: 6 }}>
            {(["all", "success", "failed", "pending"] as const).map(s => (
              <Chip key={s} active={statusFilter === s}
                label={s === "all" ? "All statuses" : s}
                onClick={() => setStatusFilter(s)}
              />
            ))}
          </div>

          {/* Divider */}
          <div style={{ width: 1, height: 24, background: "#E4E5E9", flexShrink: 0 }} />

          {/* Date range */}
          <div style={{ display: "flex", gap: 6 }}>
            {(["today", "7d", "30d"] as const).map(d => (
              <Chip key={d} active={dateRange === d}
                label={d === "today" ? "Today" : d === "7d" ? "Last 7d" : "Last 30d"}
                onClick={() => setDateRange(d)}
              />
            ))}
          </div>

          <div style={{ marginLeft: "auto", fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#9CA3AF", whiteSpace: "nowrap" }}>
            {filtered.length} entries
          </div>
        </div>

        {/* Table */}
        <div style={{
          background: "#FFFFFF", border: "1px solid #E4E5E9", borderRadius: 12,
          overflow: "hidden",
        }}>
          {/* Head */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "168px 160px 1fr 1fr 88px",
            padding: "10px 20px",
            borderBottom: "1px solid #E4E5E9",
            background: "#F7F8FA",
          }}>
            {["Timestamp", "Actor", "Action", "Target resource", "Status"].map(h => (
              <div key={h} style={{
                fontSize: 11, fontWeight: 700, color: "#9CA3AF",
                letterSpacing: "0.04em", textTransform: "uppercase",
              }}>{h}</div>
            ))}
          </div>

          {/* Rows */}
          {filtered.length === 0 ? (
            <div style={{ padding: "48px 20px", textAlign: "center" }}>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 13, color: "#9CA3AF" }}>
                No entries match the current filters.
              </div>
            </div>
          ) : (
            filtered.map((entry, i) => <AuditRow key={entry.id} entry={entry} last={i === filtered.length - 1} />)
          )}
        </div>
      </div>
    </div>
  );
}

function AuditRow({ entry, last }: { entry: AuditEntry; last: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const ss = STATUS_STYLE[entry.status];
  const ts = TYPE_STYLE[entry.actionType];
  const ac = ACTOR_TYPE_COLOR[entry.actorType];

  return (
    <>
      <div
        onClick={() => setExpanded(x => !x)}
        style={{
          display: "grid",
          gridTemplateColumns: "168px 160px 1fr 1fr 88px",
          padding: "11px 20px",
          borderBottom: last && !expanded ? "none" : "1px solid #E4E5E9",
          cursor: entry.detail ? "pointer" : "default",
          transition: "background 0.1s",
          alignItems: "center",
        }}
        onMouseEnter={e => (e.currentTarget.style.background = "#FAFBFF")}
        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
      >
        {/* Timestamp */}
        <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#6B7280" }}>
          {entry.timestamp}
        </div>

        {/* Actor */}
        <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: ac.dot, flexShrink: 0 }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 500, color: "#101114", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {entry.actor}
            </div>
            <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 9, color: "#9CA3AF" }}>{ac.label}</div>
          </div>
        </div>

        {/* Action */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            background: ts.bg, color: ts.text,
            borderRadius: 5, padding: "2px 7px",
            fontSize: 10, fontWeight: 600, whiteSpace: "nowrap",
          }}>{ts.label}</div>
          <span style={{ fontSize: 13, color: "#374151" }}>{entry.action}</span>
        </div>

        {/* Target */}
        <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "#6B7280", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {entry.target}
        </div>

        {/* Status */}
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          background: ss.bg, borderRadius: 6, padding: "3px 8px",
          width: "fit-content",
        }}>
          <div style={{ width: 5, height: 5, borderRadius: "50%", background: ss.text }} />
          <span style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 10, fontWeight: 600, color: ss.text }}>
            {ss.label}
          </span>
        </div>
      </div>

      {/* Expanded detail row */}
      {expanded && entry.detail && (
        <div style={{
          padding: "10px 20px 14px 20px",
          borderBottom: last ? "none" : "1px solid #E4E5E9",
          background: "#FAFAFA",
          animation: "expandRow 0.12s ease",
        }}>
          <style>{`@keyframes expandRow { from { opacity:0 } to { opacity:1 } }`}</style>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 9, color: "#9CA3AF", marginBottom: 4, letterSpacing: "0.05em" }}>
            DETAIL
          </div>
          <div style={{ fontSize: 12, color: "#374151", lineHeight: 1.55 }}>{entry.detail}</div>
        </div>
      )}
    </>
  );
}
