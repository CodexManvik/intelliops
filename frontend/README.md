# IntelliOps CoE — Control Plane (frontend)

The internal operator console for IntelliOps CoE — the surface the team running the system
actually looks at. Three views in one shell:

- **Overview** — the Center-of-Excellence dashboard: noise-reduction, MTTR, auto-remediation rate,
  fleet health, a live `remediation.outcomes` ticker, and evidence-based playbook graduation.
- **Incidents** — the on-call workspace: a live Situation queue and a detail panel that walks one
  incident through the six-stage pipeline, with the **HITL approval gate** as a real, pressable
  action (approve → remediate → resolve).
- **Governance** — the control plane: the three safety gates (ADR-003/007/008), the immutable audit
  trail threaded by `correlation_id`, the RBAC policy, and the playbook registry.

## Design

Apple-caliber, agency-tier: a deep instrument-panel ground, a single **signal-cyan** accent reserved
for live/resolved/focus, semantic severity colors kept separate from the accent, Geist + Geist Mono
type (self-hosted, no CDN), Double-Bezel machined cards, magnetic button-in-button CTAs, a
fluid-island floating nav, and spring-physics motion throughout on a custom cubic-bezier. View
entrances and scroll reveals are pure CSS (keyframes + `IntersectionObserver`) so they always resolve
to their visible end state; Framer Motion drives the fluid nav tab-pill and the incident detail swap.
Fully responsive; collapses to a single column below `md`. Honors `prefers-reduced-motion`.

## Data

Self-contained mock data (`src/data/`) that is **accurate to the shipped system** — the real service
ports (8001–8006), the real playbook ids and RCA ranking confidences (0.8 / 0.6 / 0.5), the 0.8
suppression threshold, the 3-success graduation rule, and the real `health_after` outcome vocabulary.
The mock module has the same shape a real `fetch`-based API client would return, so wiring it to the
live FastAPI services later is a drop-in swap behind `src/data/`.

## Run

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
npm run build    # type-check + production build to dist/
```

## Stack

React 18 · TypeScript (strict) · Vite · Tailwind CSS · Framer Motion · Phosphor Icons (light) · Geist.
