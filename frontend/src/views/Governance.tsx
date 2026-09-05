import { useMemo, useState } from "react";
import { Check, LockKey, Scroll, ShieldCheck, Sparkle, UserCheck, X } from "@phosphor-icons/react";
import { Bezel, Eyebrow, timeAgo } from "../components/primitives";
import { approveProposal, loadAudit, loadOutcomes, loadPlaybooks, loadProposals, rejectProposal } from "../data/source";
import { useLiveData } from "../hooks/useLiveData";
import { pushToast } from "../hooks/useToast";
import { Reveal as Section } from "../hooks/useReveal";
import type { AuditRow, OutcomeRow, Playbook, ProposedPlaybook } from "../data/types";

const gates = [
  {
    tone: "text-sev-crit", bg: "bg-sev-crit", icon: <LockKey size={20} weight="light" />,
    title: "RBAC, fail-closed", adr: "ADR-003",
    body: "No execution without a governance allow. An unreachable or denying gate means no action — even approval decisions are RBAC-checked.",
    reason: "denied:rbac",
  },
  {
    tone: "text-sev-warn", bg: "bg-sev-warn", icon: <ShieldCheck size={20} weight="light" />,
    title: "Reversible-only", adr: "ADR-007",
    body: "A playbook with no rollback path is refused for auto-execution. Health is verified after acting; unhealthy self-heals by rolling back.",
    reason: "refused:not-reversible",
  },
  {
    tone: "text-signal", bg: "bg-signal", icon: <UserCheck size={20} weight="light" />,
    title: "Human-in-the-loop", adr: "ADR-008",
    body: "A hitl playbook waits for an explicit approval. Reject or timeout means no action. Autonomy is earned on a spotless evidence trail.",
    reason: "aborted:timeout",
  },
];

export function Governance() {
  const PAGE = 25;
  const [shown, setShown] = useState(PAGE);
  const { data: audit } = useLiveData(loadAudit, [] as AuditRow[]);
  const { data: outcomes } = useLiveData(loadOutcomes, [] as OutcomeRow[]);
  const { data: playbooks } = useLiveData(loadPlaybooks, [] as Playbook[]);
  const { data: proposalsSeed } = useLiveData(loadProposals, [] as ProposedPlaybook[]);
  const [proposalOverrides, setProposalOverrides] = useState<Record<string, Partial<ProposedPlaybook>>>({});
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const auditSorted = [...audit].sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());

  // merge server proposals with local optimistic overrides, same pattern as
  // Incidents' situation overrides — the decision buttons update instantly,
  // the next poll/reload converges to server truth.
  const proposals = useMemo(
    () => proposalsSeed.map((p) => ({ ...p, ...proposalOverrides[p.id] })),
    [proposalsSeed, proposalOverrides],
  );
  const pendingProposals = proposals.filter((p) => p.status === "proposed");

  async function decide(proposal: ProposedPlaybook, decision: "approved" | "rejected") {
    if (decidingId) return;
    setDecidingId(proposal.id);
    const decidedBy = "oncall-alice";
    try {
      const updated =
        decision === "approved"
          ? await approveProposal(proposal.id, decidedBy)
          : await rejectProposal(proposal.id, decidedBy);
      setProposalOverrides((o) => ({ ...o, [proposal.id]: updated }));
      pushToast(
        "success",
        decision === "approved"
          ? `Approved — ${updated.playbook.name} entered the live registry`
          : `Rejected — ${updated.playbook.name} discarded`,
      );
    } catch (e) {
      pushToast("error", `${decision === "approved" ? "Approve" : "Reject"} failed: ${e instanceof Error ? e.message : "unknown"}`);
    } finally {
      setDecidingId(null);
    }
  }

  // Per-gate activity: `blocked` is the precise signal — outcomes whose reason
  // matches the gate that stopped them. `passed` is the honest total of
  // remediations that cleared every gate (reason === "healthy"); it is not
  // attributable to a single gate from this data, so all three gates share it.
  const gateStats = useMemo(() => {
    const blockedReasons: Record<string, (r: OutcomeRow["reason"]) => boolean> = {
      "denied:rbac": (r) => r === "denied:rbac",
      "refused:not-reversible": (r) => r === "refused:not-reversible",
      "aborted:timeout": (r) => r === "aborted:rejected" || r === "aborted:timeout",
    };
    const passed = outcomes.filter((o) => o.reason === "healthy").length;
    const stats: Record<string, { passed: number; blocked: number; lastTs: number | null }> = {};
    for (const g of gates) {
      const match = blockedReasons[g.reason];
      const blockedOutcomes = outcomes.filter((o) => match(o.reason));
      const lastTs = blockedOutcomes.length
        ? blockedOutcomes.reduce((max, o) => (o.ts > max ? o.ts : max), blockedOutcomes[0].ts)
        : null;
      stats[g.reason] = { passed, blocked: blockedOutcomes.length, lastTs };
    }
    return stats;
  }, [outcomes]);

  return (
    <div className="space-y-6">
      <Section>
        <Eyebrow>
          <ShieldCheck size={12} weight="light" /> Center of Excellence · control plane
        </Eyebrow>
        <h1 className="mt-4 text-4xl font-semibold tracking-tightest sm:text-5xl">
          Autonomy you can <span className="text-signal">defend to an auditor.</span>
        </h1>
        <p className="mt-3 max-w-[58ch] text-base leading-relaxed text-ink-2">
          Nothing touches production without passing three gates enforced in the call graph — not by
          convention. Every decision, executed or not, is recorded immutably.
        </p>
      </Section>

      {/* the three gates */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {gates.map((g, i) => (
          <Section key={i}>
            <div className="group relative h-full overflow-hidden rounded-4xl border border-black/[0.06] bg-black/[0.02] p-1.5 transition-transform duration-500 ease-fluid hover:-translate-y-1">
              <span className={`absolute left-1.5 top-6 h-14 w-[3px] rounded-full ${g.bg}`} />
              <div className="rounded-[calc(2rem-6px)] p-6 pl-7">
                <span className={`flex h-11 w-11 items-center justify-center rounded-2xl bg-black/[0.05] ${g.tone}`}>{g.icon}</span>
                <div className="mt-4 flex items-center gap-2">
                  <h3 className="text-lg font-semibold tracking-tight">{g.title}</h3>
                  <span className="font-mono text-2xs text-ink-3">{g.adr}</span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-ink-2">{g.body}</p>
                <div className={`mt-4 font-mono text-2xs ${g.tone}`}>→ {g.reason}</div>
                <div className="mt-3 flex items-center gap-3 border-t border-black/[0.06] pt-3 font-mono text-2xs text-ink-3">
                  <span className="text-sev-ok">✓ {gateStats[g.reason]?.passed ?? 0} passed</span>
                  <span className={g.tone}>✗ {gateStats[g.reason]?.blocked ?? 0} blocked</span>
                  {gateStats[g.reason]?.lastTs ? (
                    <span className="ml-auto">last {timeAgo(gateStats[g.reason].lastTs!)}</span>
                  ) : (
                    <span className="ml-auto">no activity yet</span>
                  )}
                </div>
              </div>
            </div>
          </Section>
        ))}
      </div>

      {/* AI-drafted proposals — the human is the gate before a draft ever reaches the registry */}
      <Section>
        <Bezel coreClassName="p-6">
          <div className="mb-4 flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
              <Sparkle size={16} weight="light" className="text-signal" />
              AI-drafted proposals · awaiting human approval
            </span>
            <span className="font-mono text-2xs text-ink-3">{pendingProposals.length} pending</span>
          </div>

          {pendingProposals.length === 0 ? (
            <div className="rounded-2xl border border-black/[0.06] p-8 text-center text-ink-3">
              No proposals waiting. A drafted runbook appears here after someone clicks{" "}
              <span className="text-ink-2">Draft a runbook with AI</span> on an incident.
            </div>
          ) : (
            <div className="space-y-3">
              {pendingProposals.map((p) => (
                <div key={p.id} className="rounded-2xl border border-signal/20 bg-signal/[0.04] p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div className="text-sm font-medium tracking-tight text-ink">{p.playbook.name}</div>
                      <div className="mt-1 flex items-center gap-3 font-mono text-2xs text-ink-3">
                        <span className="text-signal-dim">{p.id}</span>
                        {p.source_situation_id && <span>from {p.source_situation_id}</span>}
                        <span>proposed by {p.proposed_by}</span>
                        <span>{timeAgo(p.ts)}</span>
                      </div>
                    </div>
                    <span className="rounded-md bg-black/[0.05] px-2 py-0.5 font-mono text-2xs text-ink-2">
                      {p.playbook.hitl_mode} · {p.playbook.reversible ? "reversible" : "not reversible"}
                    </span>
                  </div>

                  {p.playbook.steps.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5 font-mono text-2xs text-ink-2">
                      {p.playbook.steps.map((s, i) => (
                        <span key={i} className="rounded-md bg-black/[0.05] px-2 py-0.5">
                          {s.action}
                        </span>
                      ))}
                    </div>
                  )}

                  {p.rationale && (
                    <div className="mt-2.5 rounded-lg bg-black/[0.03] p-2.5 text-2xs leading-relaxed text-ink-2">
                      <span className="font-mono text-ink-3">rationale: </span>
                      {p.rationale}
                    </div>
                  )}

                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => decide(p, "approved")}
                      disabled={decidingId === p.id}
                      className="flex items-center gap-1.5 rounded-full bg-signal px-4 py-2 text-sm font-medium text-white transition-all duration-300 ease-fluid active:scale-[0.97] disabled:opacity-50"
                    >
                      <Check size={14} weight="bold" /> Approve
                    </button>
                    <button
                      onClick={() => decide(p, "rejected")}
                      disabled={decidingId === p.id}
                      className="flex items-center gap-1.5 rounded-full border border-black/[0.10] bg-black/[0.04] px-4 py-2 text-sm text-ink-2 transition-all duration-300 ease-fluid hover:bg-black/[0.06] active:scale-[0.97] disabled:opacity-50"
                    >
                      <X size={14} weight="bold" /> Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="mt-3 border-t border-black/[0.06] pt-3 font-mono text-2xs text-ink-3">
            Approve registers the drafted playbook into the live registry (RBAC-gated, same as any approval). Reject
            discards it. Both are audited.
          </div>
        </Bezel>
      </Section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* audit log */}
        <Section className="lg:col-span-7">
          <Bezel coreClassName="p-6">
            <div className="mb-4 flex items-center justify-between gap-3">
              <span className="flex items-center gap-2 text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">
                <Scroll size={16} weight="light" className="text-ink-2" />
                Immutable audit trail · threaded by correlation_id
              </span>
              <span className="font-mono text-2xs text-ink-3">
                showing {Math.min(shown, auditSorted.length)} of {auditSorted.length}
              </span>
            </div>
            <div className="space-y-1">
              {auditSorted.slice(0, shown).map((a, i) => (
                <div key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg px-2 py-2 font-mono text-2xs transition-colors hover:bg-black/[0.03]">
                  <span className="text-ink-3">{timeAgo(a.ts)}</span>
                  <span className="truncate">
                    <span className="text-ink-2">{a.actor}</span>
                    <span className="text-ink-3"> {a.action} </span>
                    <span className="text-ink">{a.resource}</span>
                  </span>
                  <span className={`${a.decision === "deny" ? "text-sev-crit" : a.decision === "pending" ? "text-sev-warn" : "text-sev-ok"}`}>{a.decision}</span>
                </div>
              ))}
            </div>
            {shown < auditSorted.length && (
              <button onClick={() => setShown((n) => n + PAGE)} className="mt-3 w-full rounded-xl border border-black/[0.08] bg-black/[0.03] py-2 font-mono text-2xs text-ink-2 transition-colors hover:bg-black/[0.05]">
                Load {Math.min(PAGE, auditSorted.length - shown)} more
              </button>
            )}
            {auditSorted.length === 0 && (
              <div className="rounded-2xl border border-black/[0.06] p-8 text-center text-ink-3">No audit records yet — decisions appear here as the gate evaluates them.</div>
            )}
            <div className="mt-3 border-t border-black/[0.06] pt-3 font-mono text-2xs text-ink-3">
              NIST AI RMF · EU AI Act · DORA — every entry is append-only.
            </div>
          </Bezel>
        </Section>

        {/* rbac + registry */}
        <Section className="lg:col-span-5">
          <div className="space-y-4">
            <Bezel coreClassName="p-6">
              <div className="mb-4 flex items-center gap-2">
                <LockKey size={16} weight="light" className="text-ink-2" />
                <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">RBAC policy</span>
              </div>
              <div className="space-y-2.5 font-mono text-2xs">
                {[
                  { role: "operator", grant: "enrich · diagnose", who: "rca-service" },
                  { role: "operator", grant: "execute playbook:*", who: "action-service" },
                  { role: "approver", grant: "approve · reject", who: "oncall-alice" },
                  { role: "coe-admin", grant: "graduate playbook:*", who: "feedback-service" },
                ].map((r, i) => (
                  <div key={i} className="flex items-center gap-2 rounded-lg bg-black/[0.03] px-3 py-2">
                    <span className="w-20 text-signal-dim">{r.role}</span>
                    <span className="flex-1 text-ink-2">{r.grant}</span>
                    <span className="text-ink-3">{r.who}</span>
                  </div>
                ))}
              </div>
              <div className="mt-3 font-mono text-2xs text-ink-3">default: <span className="text-sev-crit">deny</span></div>
            </Bezel>

            <Bezel coreClassName="p-6">
              <div className="mb-4 flex items-center gap-2">
                <ShieldCheck size={16} weight="light" className="text-ink-2" />
                <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Playbook registry</span>
              </div>
              <div className="space-y-2">
                {playbooks.map((p) => (
                  <div key={p.id} className="flex items-center gap-2 rounded-lg bg-black/[0.03] px-3 py-2.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${p.reversible ? "bg-sev-ok" : "bg-sev-crit"}`} />
                    <span className="flex-1 text-sm text-ink">{p.name}</span>
                    <span className={`rounded-md px-2 py-0.5 font-mono text-2xs ${p.graduated ? "bg-signal/10 text-signal-dim" : "bg-black/[0.05] text-ink-2"}`}>{p.hitl_mode}</span>
                  </div>
                ))}
              </div>
            </Bezel>
          </div>
        </Section>
      </div>
    </div>
  );
}
