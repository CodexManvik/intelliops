import { LockKey, Prohibit, Scroll, ShieldCheck, UserCheck } from "@phosphor-icons/react";
import { Bezel, Eyebrow, timeAgo } from "../components/primitives";
import { loadAudit, loadPlaybooks } from "../data/source";
import { useData } from "../hooks/useData";
import { Reveal as Section } from "../hooks/useReveal";
import type { AuditRow, Playbook } from "../data/types";

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

const actorTone: Record<string, string> = {
  allow: "text-sev-ok",
  deny: "text-sev-crit",
  pending: "text-sev-warn",
};

export function Governance() {
  const { data: audit } = useData(loadAudit, [] as AuditRow[]);
  const { data: playbooks } = useData(loadPlaybooks, [] as Playbook[]);

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
            <div className="group relative h-full overflow-hidden rounded-4xl border border-white/[0.06] bg-white/[0.02] p-1.5 transition-transform duration-500 ease-fluid hover:-translate-y-1">
              <span className={`absolute left-1.5 top-6 h-14 w-[3px] rounded-full ${g.bg}`} />
              <div className="rounded-[calc(2rem-6px)] p-6 pl-7">
                <span className={`flex h-11 w-11 items-center justify-center rounded-2xl bg-white/[0.04] ${g.tone}`}>{g.icon}</span>
                <div className="mt-4 flex items-center gap-2">
                  <h3 className="text-lg font-semibold tracking-tight">{g.title}</h3>
                  <span className="font-mono text-2xs text-ink-3">{g.adr}</span>
                </div>
                <p className="mt-2 text-sm leading-relaxed text-ink-2">{g.body}</p>
                <div className={`mt-4 font-mono text-2xs ${g.tone}`}>→ {g.reason}</div>
              </div>
            </div>
          </Section>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* audit log */}
        <Section className="lg:col-span-7">
          <Bezel coreClassName="p-6">
            <div className="mb-4 flex items-center gap-2">
              <Scroll size={16} weight="light" className="text-ink-2" />
              <span className="text-2xs font-medium uppercase tracking-[0.14em] text-ink-3">Immutable audit trail · threaded by correlation_id</span>
            </div>
            <div className="space-y-1">
              {audit.map((a, i) => (
                <div key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-3 rounded-lg px-2 py-2 font-mono text-2xs transition-colors hover:bg-white/[0.025]">
                  <span className="text-ink-3">{timeAgo(a.ts)}</span>
                  <span className="truncate">
                    <span className="text-ink-2">{a.actor}</span>
                    <span className="text-ink-3"> {a.action} </span>
                    <span className="text-ink">{a.resource}</span>
                  </span>
                  <span className={`flex items-center gap-1 ${actorTone[a.decision]}`}>
                    {a.decision === "deny" && <Prohibit size={11} weight="bold" />}
                    {a.decision}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-3 border-t border-white/[0.06] pt-3 font-mono text-2xs text-ink-3">
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
                  <div key={i} className="flex items-center gap-2 rounded-lg bg-white/[0.02] px-3 py-2">
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
                  <div key={p.id} className="flex items-center gap-2 rounded-lg bg-white/[0.02] px-3 py-2.5">
                    <span className={`h-1.5 w-1.5 rounded-full ${p.reversible ? "bg-sev-ok" : "bg-sev-crit"}`} />
                    <span className="flex-1 text-sm text-ink">{p.name}</span>
                    <span className={`rounded-md px-2 py-0.5 font-mono text-2xs ${p.graduated ? "bg-signal/10 text-signal" : "bg-white/[0.05] text-ink-2"}`}>{p.hitl_mode}</span>
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
