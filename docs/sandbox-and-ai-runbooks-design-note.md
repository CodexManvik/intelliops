# Sandbox + AI-Runbooks — an honest design note

*You pushed back on three things. You were right on all three. This note owns where the
earlier research over-claimed, and lays out what we should actually build.*

Grounding (so this isn't hand-waving): everything below is checked against the real code —
`common/contracts.py` (the 4-action vocabulary), `services/action/remediate.py` (the gates),
`services/action/adapters/remediator.py` (what `dry_run` really is),
`services/action/adapters/k8s_remediator.py` + `k8s_health.py` (the real k8s path),
`architectural.md` (ADR-007 / ADR-008 / ADR-013), and `TODO.md` (the sandbox is a MEDIUM item,
not built yet).

---

## Plain-language: what a "sandbox" here actually means

A sandbox is **a safe copy to try the fix on first**, before we touch the real thing. Think of
a locksmith cutting a spare key and testing it on a practice lock before handing it to you —
same idea.

The word gets used for two very different things, and mixing them up is where the earlier
research went wrong. There are really two "sandboxes":

1. **The syntax checker (k8s "dry-run").** You hand Kubernetes the change and ask "would you
   *accept* this?" It checks the paperwork — is the name right, is the number valid, am I
   allowed — and then throws it away. **It never runs anything.** No real container starts. It's
   a spell-checker, not a rehearsal.

2. **The real rehearsal (apply-to-a-copy).** You make an actual working copy of the thing,
   apply the fix to the copy, and *watch it run* — does it come up healthy, do the numbers
   recover. This is a real rehearsal with a real result.

When you said "try it safely first, then confirm, then present" — you meant #2. The earlier
research quietly answered as if #1 counted. It doesn't. That's the first over-claim.

**Important honest note up front: today IntelliOps has NEITHER of these.** The real flow right
now (`services/action/remediate.py`) is: approve → execute on the live target → check health →
roll back if unhealthy. There is no trial step at all. `dry_run` mode
(`services/action/adapters/remediator.py`) means **"write the steps to a log and pretend it
worked"** — it never calls Kubernetes. So when we talk about "the sandbox," we are talking about
something we would be **building**, not something we have.

---

## On k8s dry-run (you were right — it's mocked) — what we'll do instead

**You were right, and it's worse than "mocked" — it's two separate weak things stacked.**

- **IntelliOps' own `dry_run`** isn't Kubernetes dry-run at all. It's `DryRunRemediator`: a
  log-only adapter that never touches the cluster. "Simulated healthy" is literally
  `return True`. So calling it a "dry run of a remediation" oversells it — it rehearses nothing.

- **Even real Kubernetes server-side dry-run** (`api.patch_namespaced_deployment(..., dry_run="All")`),
  if we swapped it in, is only an **admission check**. Kubernetes runs the request through
  validation (schema, RBAC, webhooks, quotas, PodSecurity), tells you it *would be accepted*,
  and discards it. That means:
  - no ReplicaSet is created, no pod is scheduled, no container is ever started;
  - no readiness/liveness probe ever fires;
  - **no health signal** (Meridian's `cpu_usage` / `meridian_error_rate` that our own
    `k8s_health.py` reads from Prometheus) is ever produced.

Here's the killer: our entire safety promise (ADR-007) is **"reversible-only, health-verified"**
— we only declare success after observing a *real* post-fix health signal. Dry-run
**structurally cannot** produce that signal. So dry-run can catch a typo (wrong deployment name,
bad replica count, an RBAC denial) but it can tell you **nothing** about whether the fix actually
*heals the workload*. Calling that "production-worthy validation of a remediation" was
misleading. It's a spell-check, not a rehearsal.

**What we'll do instead:** build the **real rehearsal** — apply the fix to a live copy and watch
it with the same `KubernetesHealthChecker` we already use (pod-ready + Prometheus metric
recovery). Dry-run stays useful as a *cheap first gate* ("is this even a valid request"), but it
is never the thing that says "safe." Details in the next two sections.

---

## Daytona — does it fit? (honest verdict + what we use instead)

**Short answer: no, it's the wrong shape for this job — but it's genuinely the right tool for a
different job we'll likely have later. Both halves of that are worth saying plainly.**

**What Daytona actually is.** Daytona is open-source **infrastructure for running code in a
disposable box**, built for AI agents. You call `daytona.create()` and in a fraction of a second
you get an isolated container (or micro-VM); you run shell/code inside it, read/write files,
expose a port, then `sandbox.delete()`. It's in the same family as E2B or Modal sandboxes: "give
me a throwaway machine to run a process." That's its whole world.

**Why it doesn't fit *this* job.** Our remediation target is a **Kubernetes Deployment**. To
rehearse a fix we need a Kubernetes control plane — an API server, a scheduler, a kubelet —
because we're patching a Deployment and watching real Pod/ReplicaSet health react over time.
**Daytona has none of that.** A Daytona sandbox is one box for one process; it has no API server,
no scheduler, no kubelet. To make it fit, we'd have to run a **whole nested Kubernetes cluster**
(kind / k3s) *inside* a Daytona box. That means:
- privileged / Docker-in-Docker containers (exactly what sandbox platforms tend to restrict);
- 30–90s+ to bootstrap a cluster and pull images every time (kills the "fast disposable" point);
- a brand-new external product + SDK + API dependency this project otherwise doesn't have;
- and after all that, we've just **reinvented "a kind cluster,"** the long way around.

Meanwhile we **already run kind** (recent commits: kind-up deploys Meridian, NodePort services,
Prometheus scraping the in-cluster services). We can make a real k8s copy natively in seconds
inside the cluster we already have. Daytona would be strictly slower, more fragile, and heavier.

**Where Daytona genuinely WOULD fit (worth noting for later).** The day IntelliOps has an LLM
agent that needs to **run arbitrary generated code safely** — an AI writing a Python diagnostic
script, a log-analysis snippet, a remediation-plan generator that wants to test shell commands
before recommending them — *that's* Daytona's sweet spot (or E2B/Modal). Fast, disposable,
git/SDK-friendly execution of untrusted code. That's a real future use case. It's just a
**different sandbox** than "rehearse a k8s Deployment patch," which is what we need first.

**Verdict:** Daytona = wrong tool for k8s remediation validation (no control plane; nesting a
cluster is a fragile hack). Right tool, later, for sandboxing AI-agent-generated code. **We don't
adopt it for this.** We use a k8s-native copy instead — see next section.

---

## The real sandbox recommendation (most production-worthy that's still buildable)

Four options, honestly rated on **how real** the rehearsal is and **how much effort** to build.
The winner is #1.

### Option 1 — Ephemeral namespace clone in the SAME kind cluster  ✅ RECOMMENDED

Copy the target Deployment (+ its Service/ConfigMap) into a scratch namespace like
`intelliops-sandbox-<id>`, apply the fix there, watch it with the **existing** `k8s_health.py`,
then delete the namespace.

- **How real:** *Genuinely real.* A real ReplicaSet, a real kubelet-started container, real
  readiness/liveness probes — and, critically for us, **real Prometheus-scraped metrics**
  (`cpu_usage`, `meridian_error_rate`) if the clone's pods carry the same scrape annotations
  Prometheus already discovers. It exercises the *exact* `k8s_health.py` / `k8s_remediator.py`
  code path the real fix uses. This is "does the fix work," verified — not assumed.
- **The honest limit:** it shares the **same node and same control plane** as production. So it
  does *not* catch noisy-neighbor / shared-node contention, and — this matters for us
  specifically — Meridian's own "saturation" demo fault is *resource-pressure-based*, so a shared
  node can leak contention between the sandbox pod and the real one. Mitigation that fits our
  scope: **match the sandbox pod's resource requests/limits to the real Deployment's**, and read
  a clone pass as *"this patch produces a healthy pod under real k8s scheduling and probing"* —
  true and valuable — **not** as *"this is safe under production's full concurrent load,"* which
  no same-cluster sandbox can honestly claim.
- **Effort:** **Low-to-moderate (~1–3 focused days).** The building blocks exist:
  `kind-up.sh` already applies namespaced manifests; the action service already authenticates via
  kubeconfig with Deployment/Service access; `k8s_health.py` already polls Prometheus. New work is
  mostly glue: (1) a clone/templating step (kustomize namespace overlay, or a small function using
  the existing kubernetes client to copy Deployment+Service+ConfigMap into a generated namespace);
  (2) RBAC to let the action service create/delete a `sandbox-`prefixed namespace; (3) a Prometheus
  scrape/relabel rule (or reuse pod annotations) so the clone gets scraped; (4) a teardown
  (`delete namespace`) after the health check resolves.

### Option 2 — Throwaway second kind cluster (or kind-in-kind)

- **How real:** Same realness (real container, real probes) **plus** a separate control plane and
  node, which fully removes the noisy-neighbor concern. **But** it does *not* close the deeper gap:
  a fresh cluster's node has *different* real-world load than the actual target (its own running
  workloads, its own headroom), so "passes in the throwaway cluster" still doesn't guarantee
  "passes against the real, already-loaded cluster." Arguably a *bigger* baseline-mismatch gap.
- **Effort:** **Higher.** `kind load docker-image` per cluster, duplicated manifests, its own
  Prometheus (or federation), a second kubeconfig, cluster create/wait/delete overhead (tens of
  seconds), and Docker-in-Docker headaches if the action service spins it up from inside its own
  container. More isolation, not more *information*, at meaningfully higher cost.

### Option 3 — vcluster (virtual cluster inside the host kind cluster)

- **How real:** Real containers/probes (its synced pods land on the host node), with its own API
  server/RBAC boundary — sits between namespace-clone and second-cluster. Better API-level
  isolation than a plain namespace; not node-level isolation.
- **Effort:** **Moderate-to-high**, and disproportionate here. New dependency (vcluster CLI/chart
  + lifecycle), per-sandbox kubeconfig, and the real friction: getting Prometheus to *see*
  workloads across the vcluster boundary. It's a well-built tool for **multi-tenant** isolation —
  a problem this single-demo-cluster project doesn't actually have.

### Option 4 — Dedicated sandbox platform (Daytona-style)

- **How real:** Real, but **in the wrong domain** — built to sandbox arbitrary code, not a
  Deployment inside a k8s scheduling/service-discovery fabric. To be a meaningful analog we'd have
  to rebuild a k8s control plane inside it, at which point it's "a second kind cluster," the heavy
  way. **Doesn't fit** (full argument in the Daytona section above).
- **Effort:** **High and largely wasted** for this shape of problem.

### The call

**Build Option 1.** It's the most production-worthy sandbox that's *actually buildable* in
capstone scope, and it's a clear step up from dry-run **specifically because it produces a real
pod with real probes and a real Prometheus-observed health signal**, through the same code path
the real remediation uses. State its limit out loud (shared node, so not production-isolated; not
a load test), don't paper over it. Options 2/3 remove the shared-node caveat at real cost and
still don't close the "sandbox baseline load ≠ prod baseline load" gap — only a true
staging-cluster-with-realistic-traffic setup does, and that's out of scope no matter which
mechanism we pick.

---

## AI runbook vocabulary — the honest trade-off (you were right to push back)

Your question: *if AI runbooks already run through a sandbox, why keep the format so narrow? Why
not a richer format?* Fair. Here's the honest answer, including where the earlier research
**over-stated** the case.

### First, correcting the over-statement

Today the vocabulary is a **closed, typed enum of 4 actions** (`common/contracts.py`):

```
RemediationStep.action: Literal["restart", "scale", "rollback_deploy", "wait"]
```

Each maps 1:1 to one typed `AppsV1Api` call — no shell, no string parsing (ADR-008/ADR-013,
*"exactly the kind of untyped surface a 'never delete, never do the wrong thing' guarantee can't
be built on"*). The earlier research took that good principle and concluded **"never widen."**
That was **too strong.** It conflated two *different* risks:

- **execution-mechanism risk** (untyped strings / shell-out — the thing ADR-008 rightly rejects),
  and
- **blast-radius / intent risk** (this action is destructive or simply the *wrong* fix).

Using "a sandbox can't catch everything" as a reason to add **zero** actions is the wrong
conclusion. The right one: **the sandbox changes the calculus differently for different classes of
action.** So we *can* widen — carefully, within a specific class — while permanently refusing
another class.

### What the sandbox catches vs. doesn't — the delete-namespace-runs-successfully problem

This is the crux, and it's the honest limit you were circling.

**A real apply-to-copy sandbox catches EFFECT:** does the new pod reach Ready, does the metric
recover, does a bad image crashloop, does a limits patch get OOM-killed, does the rollback target
exist. "Does the fix work," verified empirically.

**It does NOT catch SCOPE or INTENT.** And here's the trap:

> A `delete namespace` (or `scale-to-0`) runs **successfully in the sandbox too.** The copy gets
> deleted, or scales to zero — no error, nothing crashes. A naive health check sees "no pods
> failing" and reports **PASS**. The catastrophic action and the clean action are
> **indistinguishable to the sandbox.**

The sandbox can't tell "deleted a disposable clone" from "deleted the thing serving prod
traffic." It can't tell whether this was the *right* fix or a plausible-sounding wrong one an LLM
authored. It can't replicate cluster-scoped state (a ClusterRole edit, a CRD change — copying "the
namespace" tests only a fragment). And our health checker only observes *pod-readiness + a metric
predicate* — it has **no way** to see "the fix quietly wrote garbage to an external system."

So: **a catastrophic action that is also a *clean* action sails through as a pass, every time.**
That is exactly why "the sandbox validated it" must **never** become the justification for
auto-approving a destructive action.

### Where sandbox + approval + typed-actions lets us safely land

Given we have three gates (typed vocabulary + human approval + — once built — the real sandbox),
here's the honest per-class breakdown. The dividing line is **not** "typed vs. untyped" in the
abstract. It's whether the action's failure mode is **(a) observable** by pod-readiness + metric
recovery on a bounded copy, **and (b) reversible** by a same-shape undo. Delete / exec / secrets /
cluster-scope fail one or both.

**SAFE-ISH TO ADD — widen from 4 to ~9-10 (still a closed `Literal[...]`, still 1 action → 1
typed API call):**

| Add | Why it's safe to add | Sandbox does real work because… |
|---|---|---|
| `patch_resource_limits` (cpu/mem) | one deployment; failure = OOMKill / unschedulable | that's exactly what pod-ready catches |
| `rollback_to_revision(N)` | same mechanism as existing rollback, parameterized | sandbox confirms the revision exists & comes up healthy |
| `cordon_node` / `uncordon_node` | reversible, no data loss | sandbox validates pods reschedule |
| `patch_hpa` (min/max/target) | bounded, reversible by re-patch | sandbox catches an HPA that immediately thrashes |
| `patch_probe` (readiness/liveness timing) | narrow, reversible | directly testable by the existing checker |

These share one profile: **single bounded resource, no data deletion, an undo of the same shape**
(so ADR-007's `reversible` + `rollback_steps` requirement extends *naturally*), and a failure mode
`KubernetesHealthChecker` can actually observe. Adding the sandbox to this tier is a **genuine
safety upgrade** — it catches misconfigurations before prod precisely because these failures are
observable.

**REMAIN DANGEROUS EVEN WITH A SANDBOX — a "pass" here is false confidence, exclude permanently:**

- **`delete` anything** (namespace, deployment, PVC, secret) — the clean-pass trap above.
- **`scale_to(0)` / unbounded absolute scale** — same shape as delete; "no pods" can look like a
  valid terminal state. (Keep the existing **delta-based** `+N/-N` scale; do **not** add absolute
  `scale_to`.)
- **`exec` / arbitrary command in a pod** — reintroduces the untyped-string/shell surface ADR-008
  exists to reject. "Sandbox passed exec" only means the command didn't error in the copy — says
  nothing about what it *did* (could curl an external endpoint, touch a shared DB).
- **Secret create/patch/read** — either real creds shared with prod (copy isn't isolated for
  anything reaching an external system) or fake creds (the test proves nothing). Sandboxing a
  secret action is near-meaningless.
- **Cluster-scoped ops** (ClusterRole*, CRDs, admission webhooks, namespace lifecycle) — a
  namespace-copy structurally can't replicate cluster-scoped state, so it only ever tests a
  fragment of the blast radius.
- **Anything whose failure is "silently wrong data / wrong external side effect"** rather than
  "pod not Ready" — the health checker is blind to it.

### The honest spectrum (corrected)

1. **Closed-typed-4 (today):** safest, paired with post-hoc health-check + rollback, **no
   sandbox**. Least capable — can't even express limits/HPA/cordon.
2. **Typed action pack (the ~6 additions):** still a closed `Literal[...]`, still 1:1 typed API
   calls. **Adding the sandbox here is a real upgrade** — these failures are observable.
   ← *This is the move sandbox + approval honestly unlocks.*
3. **Typed set that ALSO includes delete/exec/scale-to-0/secrets/cluster-scope:** "typed" but with
   catastrophic-clean-pass failure modes. **Sandbox gives *false* confidence here — worse than no
   sandbox,** because "sandbox validated it" becomes a stated reason to auto-approve something
   never checked for the property that matters (blast radius). **Don't add, sandbox or not.**
4. **Fully open DSL (Ansible-style / free-form shell):** most capable, sandbox value drops fastest
   — it can express tier-3 *and* chain it *and* do the "silently wrong data" failure the health
   checker can't see. **This is what ADR-008 was written against; a sandbox does not change that.**

**So, plainly:** sandbox + human approval buys you **tier 1 → tier 2** — a materially richer,
still-fully-typed vocabulary, with the sandbox doing real work. It does **not** buy safe passage
to tier 3 or 4, because their risk is blast-radius and intent, which is a *different axis* than
"does the health check pass," and no amount of sandboxing changes what axis a mechanism measures.
The earlier "richer format is fine because sandbox" instinct is right for tier 2 and wrong for
tiers 3–4.

---

## Recommended plan of attack (phased) + what YOU must decide

All changes are **additive** to the existing action/governance/read/UI — we extend the enum, add
a gate, add a contract field, project it through the read model, render one UI row. Nothing
existing is rewritten.

### Phase 0 — Correct the record (docs only, ~½ day)
- Amend the earlier research note: `dry_run` is log-only, not a rehearsal; k8s server-side dry-run
  is admission-only, not health validation; "never widen" was too strong (see tiers above).
- Land this note in `docs/`. No code.

### Phase 1 — Build the real sandbox (Option 1, ~1–3 days)
- **Pick apply-to-copy, not `--dry-run=server` alone.** Admission dry-run can't observe runtime
  health, so it can't validate the new actions' failure modes (OOMKill, HPA thrash, probe flap).
  Keep dry-run as a cheap *first* gate; the clone is what says "safe."
- Clone Deployment+Service+ConfigMap into `intelliops-sandbox-<id>`; **reuse
  `KubernetesHealthChecker`** (pod-ready + metric predicate) against the copy; match resource
  requests/limits to the real Deployment; teardown on resolve.
- Add a `preflight()` step in `execute_remediation` **between HITL-approval and execute** (the
  `TODO.md` MEDIUM item already scopes this seam). Additive `preflight` result field on the
  outcome contract; project through the read model; render one **"pre-flight ✓ validated before
  executed"** row in the incident timeline.

### Phase 2 — One structural (non-sandbox) guard (~½ day)
- Add a **hard-coded destructive/unbounded denylist** check that runs **BEFORE** the sandbox and
  blocks `delete` / absolute-or-zero scale / `exec` / secret ops / cluster-scoped ops **outright**.
- The point: the safety story becomes *"the typed vocabulary excludes catastrophic actions by
  construction, AND the survivors are additionally effect-verified by the sandbox"* — never
  *"the sandbox verified it, therefore it's safe."* This guard is orthogonal to sandboxing on
  purpose.

### Phase 3 — Widen the vocabulary, tier-2 only (~1–2 days)
- Extend the `Literal[...]` from 4 to ~9-10: keep `restart, scale (delta only), rollback_deploy,
  wait`; add `patch_resource_limits, rollback_to_revision, cordon_node, uncordon_node, patch_hpa,
  patch_probe`. Each new action = one typed `AppsV1Api` call, and **each needs a same-shape typed
  rollback step** so ADR-007's `reversible` + `rollback_steps` invariant extends rather than
  weakens.
- Every new action ships behind **HITL by default** (ADR-008) — it graduates to auto only on a
  measured track record, exactly as today.

### What YOU must decide

1. **Sandbox mechanism — confirm Option 1 (namespace clone).** Recommended. Say yes, or pick 2/3
   knowing the extra cost buys isolation, not more information. (Daytona/Option 4: **no** — wrong
   tool; reconsider only if/when we add an AI-code-execution feature.)
2. **How far to widen — tier 2 only?** Recommend stopping at ~9-10 typed actions. Confirm we are
   **permanently** (not "not yet") excluding delete / absolute-scale / exec / secrets /
   cluster-scope from the AI-authorable path. If those are ever needed, they belong behind a
   *separate, human-typed* path — never an AI-authored auto-approvable step.
3. **Does pre-flight block, or advise?** i.e. if the sandbox rehearsal *fails*, do we hard-stop the
   remediation, or surface the failure to the human approver and let them decide? (Recommend:
   **block on sandbox failure for `auto` playbooks; advise the human for `hitl` playbooks.**)
4. **Scope for the capstone.** Phases 0–1 alone already deliver the honest "try it safely first,
   then confirm, then present" flow you asked for. Phases 2–3 are the vocabulary widening. Confirm
   whether all four phases are in scope, or just 0–1 for the demo.
