import { useState, useMemo, type KeyboardEvent } from "react";
import { loadAudit } from "../data/source";
import { useData } from "../hooks/useData";
import type { AuditRow as AuditRecord } from "../data/types";

// The page renders the REAL governance audit trail (GET /audit, or the mock
// rows in mock mode). These are the view-model types the table works in.
type AuditActionType = "playbook" | "situation" | "other";
type AuditStatus = "success" | "failed" | "pending";

interface AuditEntry {
  id: string;
  tsMs: number;
  timestamp: string;
  actor: string;
  actorType: "user" | "system";
  action: string;
  actionType: AuditActionType;
  target: string;
  status: AuditStatus;
  detail: string;
}

const pad = (n: number) => String(n).padStart(2, "0");

function toEntry(r: AuditRecord, i: number): AuditEntry {
  const d = new Date(r.ts);
  const kind = r.resource.split(":")[0];
  return {
    id: `${r.correlation_id}-${r.ts}-${i}`,
    tsMs: d.getTime(),
    timestamp: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
    actor: r.actor,
    // Platform services audit under their service name; anything else is a person.
    actorType: r.actor.endsWith("-service") ? "system" : "user",
    action: r.action,
    actionType: kind === "playbook" || kind === "situation" ? kind : "other",
    target: r.resource,
    status: r.decision === "allow" ? "success" : r.decision === "deny" ? "failed" : "pending",
    detail: `Decision: ${r.decision} · correlation id: ${r.correlation_id}`,
  };
}

const RANGE_MS = { today: 0, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000 } as const;

function exportCsv(rows: AuditEntry[]) {
  const cell = (v: string) => `"${v.replace(/"/g, '""')}"`;
  const lines = [
    ["timestamp", "actor", "action", "target", "status"].join(","),
    ...rows.map(e => [e.timestamp, e.actor, e.action, e.target, e.status].map(cell).join(",")),
  ];
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "intelliops-audit.csv";
  a.click();
  URL.revokeObjectURL(url);
}

// ─── Badges ───────────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<AuditStatus, { bg: string; text: string; label: string }> = {
  success: { bg: "var(--pg-ok-soft)", text: "var(--pg-ok-ink)", label: "success" },
  failed:  { bg: "var(--pg-crit-soft)", text: "var(--pg-crit-ink)", label: "failed"  },
  pending: { bg: "var(--pg-warn-soft)", text: "var(--pg-warn-ink)", label: "pending" },
};

const TYPE_STYLE: Record<AuditActionType, { bg: string; text: string; label: string }> = {
  playbook:  { bg: "var(--pg-accent-soft)", text: "var(--pg-indigo-ink)", label: "Playbook"  },
  situation: { bg: "var(--pg-crit-soft)", text: "var(--pg-crit-ink)", label: "Situation" },
  other:     { bg: "var(--pg-purple-soft)", text: "var(--pg-purple)", label: "Other"     },
};

const ACTOR_TYPE_COLOR: Record<string, { dot: string; label: string }> = {
  user:   { dot: "var(--pg-accent)", label: "user"   },
  system: { dot: "var(--pg-ink-3)", label: "system" },
};

// ─── Filter chip ──────────────────────────────────────────────────────────────

function Chip({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} aria-pressed={active} style={{
      padding: "5px 12px", borderRadius: 7,
      fontSize: 13, fontWeight: active ? 600 : 500,
      background: active ? "var(--pg-accent)" : "var(--pg-subtle)",
      color: active ? "var(--pg-on-accent)" : "var(--pg-ink-3)",
      border: active ? "1px solid var(--pg-accent)" : "1px solid var(--pg-line)",
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
  const [dateRange, setDateRange] = useState<"today" | "7d" | "30d">("7d");
  const { data, loading, error } = useData(loadAudit, []);

  const filtered = useMemo(() => {
    const since = dateRange === "today" ? new Date().setHours(0, 0, 0, 0) : Date.now() - RANGE_MS[dateRange];
    const entries = data.map(toEntry).sort((a, b) => b.tsMs - a.tsMs);
    return entries.filter(e => {
      if (e.tsMs < since) return false;
      if (actionType !== "all" && e.actionType !== actionType) return false;
      if (statusFilter !== "all" && e.status !== statusFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        return (
          e.actor.toLowerCase().includes(q) ||
          e.action.toLowerCase().includes(q) ||
          e.target.toLowerCase().includes(q) ||
          e.detail.toLowerCase().includes(q)
        );
      }
      return true;
    });
  }, [data, search, actionType, statusFilter, dateRange]);

  return (
    <div style={{ minHeight: "100vh", background: "var(--pg-page)", paddingTop: 60 }}>
      {/* Page header */}
      <div style={{
        background: "var(--pg-card)", borderBottom: "1px solid var(--pg-line)",
        padding: "28px clamp(16px, 4vw, 40px) 24px",
      }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
            <div>
              <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "var(--pg-ink-4)", marginBottom: 6, letterSpacing: "0.05em" }}>
                SYSTEM / AUDIT LOG
              </div>
              <h1 style={{ fontSize: 22, fontWeight: 800, color: "var(--pg-ink)", margin: 0, letterSpacing: "-0.03em" }}>
                Audit Log
              </h1>
              <p style={{ fontSize: 13, color: "var(--pg-ink-3)", margin: "4px 0 0" }}>
                Every decision the platform and its operators made: approvals, executions, escalations.
              </p>
            </div>
            <button onClick={() => exportCsv(filtered)} disabled={filtered.length === 0} style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "8px 14px", borderRadius: 8, border: "1px solid var(--pg-line)",
              background: "var(--pg-card)", color: "var(--pg-ink-2)", fontSize: 13, fontWeight: 500,
              cursor: "pointer", transition: "border-color 0.15s",
            }}
            onMouseEnter={e => (e.currentTarget.style.borderColor = "var(--pg-accent-line)")}
            onMouseLeave={e => (e.currentTarget.style.borderColor = "var(--pg-line)")}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M7 2l5 5-5 5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              Export CSV
            </button>
          </div>
        </div>
      </div>

      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px clamp(16px, 4vw, 40px)" }}>
        {/* Filter bar */}
        <div style={{
          background: "var(--pg-card)", border: "1px solid var(--pg-line)", borderRadius: 12,
          padding: "14px 16px", marginBottom: 16,
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
        }}>
          {/* Search */}
          <div style={{ position: "relative", flexShrink: 0 }}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"
              style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--pg-ink-4)" }}>
              <circle cx="6" cy="6" r="4" stroke="currentColor" strokeWidth="1.25"/>
              <path d="m9.5 9.5 2.5 2.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round"/>
            </svg>
            <input
              type="text"
              aria-label="Search the audit trail"
              placeholder="Search actions, actors, targets…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{
                paddingLeft: 30, paddingRight: 12, paddingTop: 7, paddingBottom: 7,
                border: "1px solid var(--pg-line)", borderRadius: 7, fontSize: 13,
                color: "var(--pg-ink)", background: "var(--pg-page)", width: 240, maxWidth: "100%",
                fontFamily: "inherit", outline: "none",
                transition: "border-color 0.15s",
              }}
              onFocus={e => (e.currentTarget.style.borderColor = "var(--pg-accent)")}
              onBlur={e => (e.currentTarget.style.borderColor = "var(--pg-line)")}
            />
          </div>

          {/* Divider */}
          <div style={{ width: 1, height: 24, background: "var(--pg-line)", flexShrink: 0 }} />

          {/* Action type chips */}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(["all", "playbook", "situation", "other"] as const).map(t => (
              <Chip key={t} active={actionType === t}
                label={t === "all" ? "All types" : TYPE_STYLE[t as AuditActionType]?.label ?? t}
                onClick={() => setActionType(t)}
              />
            ))}
          </div>

          {/* Divider */}
          <div style={{ width: 1, height: 24, background: "var(--pg-line)", flexShrink: 0 }} />

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
          <div style={{ width: 1, height: 24, background: "var(--pg-line)", flexShrink: 0 }} />

          {/* Date range */}
          <div style={{ display: "flex", gap: 6 }}>
            {(["today", "7d", "30d"] as const).map(d => (
              <Chip key={d} active={dateRange === d}
                label={d === "today" ? "Today" : d === "7d" ? "Last 7d" : "Last 30d"}
                onClick={() => setDateRange(d)}
              />
            ))}
          </div>

          <div style={{ marginLeft: "auto", fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "var(--pg-ink-4)", whiteSpace: "nowrap" }}>
            {filtered.length} entries
          </div>
        </div>

        {/* Table */}
        <div style={{
          background: "var(--pg-card)", border: "1px solid var(--pg-line)", borderRadius: 12,
          overflowX: "auto",
        }}>
          {/* The five fixed columns need ~720px: scroll inside the card, not the page. */}
          <div style={{ minWidth: 720 }}>
          {/* Head */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "168px 160px 1fr 1fr 88px",
            padding: "10px 20px",
            borderBottom: "1px solid var(--pg-line)",
            background: "var(--pg-page)",
          }}>
            {["Timestamp", "Actor", "Action", "Target resource", "Status"].map(h => (
              <div key={h} style={{
                fontSize: 11, fontWeight: 700, color: "var(--pg-ink-4)",
                letterSpacing: "0.04em", textTransform: "uppercase",
              }}>{h}</div>
            ))}
          </div>

          {/* Rows */}
          {filtered.length === 0 ? (
            <div style={{ padding: "48px 20px", textAlign: "center" }}>
              <div role={error ? "alert" : "status"} style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 13, color: error ? "var(--pg-crit-ink)" : "var(--pg-ink-4)" }}>
                {error
                  ? "Could not load the audit trail from the governance service."
                  : loading
                    ? "Loading audit trail…"
                    : "No entries match the current filters."}
              </div>
            </div>
          ) : (
            filtered.map((entry, i) => <AuditRow key={entry.id} entry={entry} last={i === filtered.length - 1} />)
          )}
          </div>
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
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded(x => !x)}
        onKeyDown={(e: KeyboardEvent) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded(x => !x);
          }
        }}
        style={{
          display: "grid",
          gridTemplateColumns: "168px 160px 1fr 1fr 88px",
          padding: "11px 20px",
          borderBottom: last && !expanded ? "none" : "1px solid var(--pg-line)",
          cursor: "pointer",
          transition: "background 0.1s",
          alignItems: "center",
        }}
        onMouseEnter={e => (e.currentTarget.style.background = "var(--pg-page)")}
        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
      >
        {/* Timestamp */}
        <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "var(--pg-ink-3)" }}>
          {entry.timestamp}
        </div>

        {/* Actor */}
        <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
          <div style={{ width: 6, height: 6, borderRadius: "50%", background: ac.dot, flexShrink: 0 }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 500, color: "var(--pg-ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {entry.actor}
            </div>
            <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 9, color: "var(--pg-ink-4)" }}>{ac.label}</div>
          </div>
        </div>

        {/* Action */}
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            background: ts.bg, color: ts.text,
            borderRadius: 5, padding: "2px 7px",
            fontSize: 10, fontWeight: 600, whiteSpace: "nowrap",
          }}>{ts.label}</div>
          <span style={{ fontSize: 13, color: "var(--pg-ink-2)" }}>{entry.action}</span>
        </div>

        {/* Target */}
        <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 11, color: "var(--pg-ink-3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
      {expanded && (
        <div style={{
          padding: "10px 20px 14px 20px",
          borderBottom: last ? "none" : "1px solid var(--pg-line)",
          background: "var(--pg-page)",
          animation: "expandRow 0.12s ease",
        }}>
          <style>{`@keyframes expandRow { from { opacity:0 } to { opacity:1 } }`}</style>
          <div style={{ fontFamily: "JetBrains Mono, monospace", fontSize: 9, color: "var(--pg-ink-4)", marginBottom: 4, letterSpacing: "0.05em" }}>
            DETAIL
          </div>
          <div style={{ fontSize: 12, color: "var(--pg-ink-2)", lineHeight: 1.55 }}>{entry.detail}</div>
        </div>
      )}
    </>
  );
}
