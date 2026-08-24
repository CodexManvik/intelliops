import { useMemo, useState } from "react";
import { Funnel, ListMagnifyingGlass, Prohibit } from "@phosphor-icons/react";
import { Bezel, Eyebrow, timeAgo } from "../components/primitives";
import { loadAudit } from "../data/source";
import { useLiveData } from "../hooks/useLiveData";
import { Reveal as Section } from "../hooks/useReveal";
import type { AuditRow } from "../data/types";

const actorTone: Record<string, string> = {
  allow: "text-sev-ok",
  deny: "text-sev-crit",
  pending: "text-sev-warn",
};

type DecisionFilter = "all" | AuditRow["decision"];

const decisionOptions: { id: DecisionFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "allow", label: "Allow" },
  { id: "deny", label: "Deny" },
  { id: "pending", label: "Pending" },
];

export function Audit() {
  const { data: audit } = useLiveData(loadAudit, [] as AuditRow[]);
  const [actor, setActor] = useState("");
  const [correlationId, setCorrelationId] = useState("");
  const [decision, setDecision] = useState<DecisionFilter>("all");

  const filtered = useMemo(() => {
    const actorQ = actor.trim().toLowerCase();
    const corrQ = correlationId.trim().toLowerCase();
    return audit.filter((a) => {
      if (decision !== "all" && a.decision !== decision) return false;
      if (actorQ && !a.actor.toLowerCase().includes(actorQ)) return false;
      if (corrQ && !a.correlation_id.toLowerCase().includes(corrQ)) return false;
      return true;
    });
  }, [audit, actor, correlationId, decision]);

  const hasData = audit.length > 0;
  const hasMatches = filtered.length > 0;

  return (
    <div className="space-y-6">
      <Section>
        <Eyebrow>
          <ListMagnifyingGlass size={12} weight="light" /> Audit-trail explorer
        </Eyebrow>
        <h1 className="mt-4 text-4xl font-semibold tracking-tightest sm:text-5xl">
          Every decision, <span className="text-signal">searchable.</span>
        </h1>
        <p className="mt-3 max-w-[58ch] text-base leading-relaxed text-ink-2">
          Filter the immutable audit trail by actor, decision, or correlation_id to reconstruct exactly
          what happened, who asked, and what the gate decided.
        </p>
      </Section>

      <Section>
        <Bezel coreClassName="p-5">
          <div className="mb-3 flex items-center gap-2">
            <Funnel size={16} weight="light" className="text-ink-2" />
            <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Filters</span>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <label className="block">
              <span className="mb-1.5 block text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Actor
              </span>
              <input
                type="text"
                value={actor}
                onChange={(e) => setActor(e.target.value)}
                placeholder="e.g. oncall-alice"
                className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 font-mono text-2xs text-ink placeholder:text-ink-4 focus:border-signal/40 focus:outline-none focus:ring-2 focus:ring-signal/15"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 block text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Correlation ID
              </span>
              <input
                type="text"
                value={correlationId}
                onChange={(e) => setCorrelationId(e.target.value)}
                placeholder="e.g. corr-9f2a"
                className="w-full rounded-xl border border-black/[0.08] bg-white px-3 py-2 font-mono text-2xs text-ink placeholder:text-ink-4 focus:border-signal/40 focus:outline-none focus:ring-2 focus:ring-signal/15"
              />
            </label>

            <div>
              <span className="mb-1.5 block text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Decision
              </span>
              <div className="inline-flex items-center gap-1 rounded-xl border border-black/[0.08] bg-black/[0.03] p-1">
                {decisionOptions.map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => setDecision(opt.id)}
                    aria-pressed={decision === opt.id}
                    className={`rounded-lg px-3 py-1.5 font-mono text-2xs transition-colors duration-300 ${
                      decision === opt.id
                        ? "bg-white text-ink shadow-lift"
                        : "text-ink-3 hover:text-ink-2"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Bezel>
      </Section>

      <Section>
        <Bezel coreClassName="p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ListMagnifyingGlass size={16} weight="light" className="text-ink-2" />
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                Audit trail · threaded by correlation_id
              </span>
            </div>
            <span className="font-mono text-2xs text-ink-3">
              {filtered.length} of {audit.length} records
            </span>
          </div>

          {hasData && hasMatches && (
            <div className="space-y-1">
              {filtered.map((a, i) => (
                <div
                  key={i}
                  className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg px-2 py-2 font-mono text-2xs transition-colors hover:bg-black/[0.03]"
                >
                  <span className="text-ink-3">{timeAgo(a.ts)}</span>
                  <span className="truncate">
                    <span className="text-ink-2">{a.actor}</span>
                    <span className="text-ink-3"> {a.action} </span>
                    <span className="text-ink">{a.resource}</span>
                    <span className="text-signal-dim"> · {a.correlation_id}</span>
                  </span>
                  <span className={`flex items-center gap-1 ${actorTone[a.decision]}`}>
                    {a.decision === "deny" && <Prohibit size={11} weight="bold" />}
                    {a.decision}
                  </span>
                </div>
              ))}
            </div>
          )}

          {hasData && !hasMatches && (
            <div className="rounded-2xl border border-black/[0.06] p-10 text-center text-ink-3">
              No records match these filters — try widening the actor, decision, or correlation_id search.
            </div>
          )}

          {!hasData && (
            <div className="rounded-2xl border border-black/[0.06] p-10 text-center text-ink-3">
              No audit records yet — decisions will appear here as the gate evaluates them.
            </div>
          )}

          <div className="mt-3 border-t border-black/[0.06] pt-3 font-mono text-2xs text-ink-3">
            NIST AI RMF · EU AI Act · DORA — every entry is append-only.
          </div>
        </Bezel>
      </Section>
    </div>
  );
}
