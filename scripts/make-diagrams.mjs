/* Architecture and flow diagrams for IntelliOps.
 *
 * Emits SVG into docs/diagrams/, one file per diagram per theme. Hand-built
 * rather than Mermaid because these have to sit in the capstone deck next to
 * slides that were designed, and Mermaid's auto-layout cannot be made to match
 * a palette and a type scale. Everything here is deterministic: same input,
 * byte-identical output, so a regenerated diagram diffs cleanly.
 *
 * The topology is not invented. It was read out of the services:
 *   ingestion   -> telemetry.raw
 *   correlation -> situations.detected, situations.suppressed
 *   rca         -> situations.diagnosed
 *   action      -> remediation.outcomes
 *   feedback, read, governance all consume remediation.outcomes
 *
 * Fonts are the deck's: Arial for labels, Courier New for anything that is a
 * real identifier (topic names, table names, verbs). Both render true-to-width
 * everywhere, which matters because SVG text does not wrap or reflow.
 *
 * Run: node scripts/make-diagrams.mjs
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "docs", "diagrams");

/* --- palettes, lifted from frontend/src/styles/index.css ------------------ */
const THEMES = {
  dark: {
    bg: "#0A0A0A",
    card: "#111111",
    surface: "#161616",
    line: "#242424",
    lineStrong: "#2E2E2E",
    ink: "#EDEDED",
    ink2: "#B4B4B4",
    ink3: "#9C9C9C",
    signal: "#52A8FF",
    ok: "#62C073",
    warn: "#FFB224",
    crit: "#FF6369",
    attention: "#BF7AF0",
  },
  light: {
    bg: "#FFFFFF",
    card: "#FAFAFA",
    surface: "#F2F2F2",
    line: "#EAEAEA",
    lineStrong: "#D6D6D6",
    ink: "#171717",
    ink2: "#4A4A4A",
    ink3: "#5C5C5C",
    signal: "#0060C4",
    ok: "#146843",
    warn: "#8A5000",
    crit: "#B81E24",
    attention: "#7C3AAD",
  },
};

const SANS = "Arial, Helvetica, sans-serif";
const MONO = "'Courier New', Courier, monospace";

/* Average glyph width as a fraction of font size, so boxes can be sized to
 * their content. Arial is ~0.52, Courier New is monospaced at exactly 0.60. */
const W_SANS = 0.52;
const W_MONO = 0.6;
const textW = (s, size, mono) => s.length * size * (mono ? W_MONO : W_SANS);

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/* Every solid box registers itself here, and the generator refuses to write a
 * diagram whose boxes overlap or fall outside the canvas. SVG has no layout
 * engine, so a coordinate that is 80px wrong produces a picture that is subtly
 * unreadable rather than an error, and the first cut of the architecture
 * diagram put `action` underneath `read model` for exactly that reason.
 * Dashed grouping bands are exempt: containing other boxes is their job.
 */
let BOXES = [];
const track = (x, y, w, h, name) => BOXES.push({ x, y, w, h, name });

function validate(name, w, h) {
  const bad = [];
  for (const b of BOXES) {
    if (b.x < 0 || b.y < 0 || b.x + b.w > w || b.y + b.h > h) {
      bad.push(`  ${b.name} outside the canvas`);
    }
  }
  for (let i = 0; i < BOXES.length; i++) {
    for (let j = i + 1; j < BOXES.length; j++) {
      const a = BOXES[i];
      const b = BOXES[j];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 1 && oy > 1) {
        bad.push(`  ${a.name} overlaps ${b.name} by ${Math.round(ox)}x${Math.round(oy)}px`);
      }
    }
  }
  if (bad.length) {
    console.error(`FAIL ${name}:`);
    bad.forEach((l) => console.error(l));
    return false;
  }
  return true;
}

/* --- primitives ---------------------------------------------------------- */

function text(x, y, s, o = {}) {
  const size = o.size || 15;
  return (
    `<text x="${x}" y="${y}" font-family="${o.mono ? MONO : SANS}" font-size="${size}" ` +
    `${o.bold ? 'font-weight="700" ' : ""}fill="${o.fill}" ` +
    `text-anchor="${o.anchor || "start"}"` +
    `${o.spacing ? ` letter-spacing="${o.spacing}"` : ""}>${esc(s)}</text>`
  );
}

/* A labelled node. `kind` picks the accent; `tag` is the small mono line above
 * the title, `sub` the muted line below it. */
function node(t, x, y, w, h, o) {
  const accent = o.accent || null;
  const fill = o.emphasis ? t.surface : t.card;
  const stroke = accent || t.lineStrong;
  track(x, y, w, h, o.title);
  const parts = [
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="${fill}" ` +
      `stroke="${stroke}" stroke-width="${accent ? 1.6 : 1}"/>`,
  ];
  let ty = y + 26;
  if (o.tag) {
    parts.push(
      text(x + 16, ty, o.tag.toUpperCase(), {
        size: 10,
        mono: true,
        bold: true,
        spacing: 1.2,
        fill: accent || t.ink3,
      }),
    );
    // Proportional to the title that follows, not a constant. A 30px title
    // under a fixed 22px step overlaps its own tag, which is what the
    // earned-autonomy diagram was doing.
    ty += Math.max(22, (o.titleSize || 17) + 8);
  }
  parts.push(text(x + 16, ty, o.title, { size: o.titleSize || 17, bold: true, fill: t.ink }));
  ty += o.sub ? 20 : 0;
  if (o.sub) parts.push(text(x + 16, ty, o.sub, { size: 12, fill: t.ink3, mono: o.subMono }));
  if (o.sub2) parts.push(text(x + 16, ty + 17, o.sub2, { size: 12, fill: t.ink3, mono: o.subMono }));
  return parts.join("\n  ");
}

/* An arrow with an optional label riding on it. `dashed` marks an HTTP call
 * rather than a bus hop, which is the distinction that matters in this system. */
function arrow(t, x1, y1, x2, y2, o = {}) {
  const color = o.color || t.ink3;
  const id = o.headId;
  const parts = [
    `<path d="M${x1},${y1} L${x2},${y2}" stroke="${color}" stroke-width="${o.width || 1.4}" ` +
      `fill="none"${o.dashed ? ' stroke-dasharray="6 5"' : ""} marker-end="url(#${id})"/>`,
  ];
  if (o.label) {
    const mx = (x1 + x2) / 2 + (o.lx || 0);
    const my = (y1 + y2) / 2 + (o.ly || -9);
    const tw = textW(o.label, 11, true) + 14;
    track(mx - tw / 2, my - 12, tw, 17, `label:${o.label}`);
    parts.push(
      `<rect x="${mx - tw / 2}" y="${my - 12}" width="${tw}" height="17" rx="4" fill="${t.bg}"/>`,
    );
    parts.push(text(mx, my, o.label, { size: 11, mono: true, fill: color, anchor: "middle" }));
  }
  return parts.join("\n  ");
}

/* A decision diamond. Registered as its bounding box so the geometry gate
 * still applies, even though the drawn shape is inscribed in it. */
function diamond(t, cx, cy, w, h, label, sub) {
  track(cx - w / 2, cy - h / 2, w, h, `decide:${label}`);
  const pts = [
    `${cx},${cy - h / 2}`,
    `${cx + w / 2},${cy}`,
    `${cx},${cy + h / 2}`,
    `${cx - w / 2},${cy}`,
  ].join(" ");
  const out = [
    `<polygon points="${pts}" fill="${t.card}" stroke="${t.ink3}" stroke-width="1.2"/>`,
    text(cx, cy + 1, label, { size: 13, bold: true, fill: t.ink, anchor: "middle" }),
  ];
  if (sub) out.push(text(cx, cy + 18, sub, { size: 10.5, mono: true, fill: t.ink3, anchor: "middle" }));
  return out.join("\n  ");
}

function band(t, x, y, w, h, label) {
  return (
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="14" fill="none" ` +
    `stroke="${t.line}" stroke-width="1" stroke-dasharray="3 4"/>\n  ` +
    text(x + 16, y + 20, label.toUpperCase(), {
      size: 10,
      mono: true,
      bold: true,
      spacing: 1.4,
      fill: t.ink3,
    })
  );
}

function svg(t, w, h, title, subtitle, body) {
  const heads = ["ink3", "signal", "ok", "crit", "warn", "attention"]
    .map(
      (k) =>
        `<marker id="h-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" ` +
        `markerHeight="7" orient="auto-start-reverse">` +
        `<path d="M0,1 L10,5 L0,9 z" fill="${t[k]}"/></marker>`,
    )
    .join("\n    ");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(title)}">
  <defs>
    ${heads}
  </defs>
  <rect width="${w}" height="${h}" fill="${t.bg}"/>
  ${text(48, 56, title, { size: 26, bold: true, fill: t.ink })}
  ${text(48, 82, subtitle, { size: 14, fill: t.ink3 })}
  ${body}
</svg>
`;
}

/* ========================================================================= *
 * 1. System architecture
 * ========================================================================= */
function architecture(t) {
  const H = "h-ink3";
  const p = [];
  const W = 2200;

  // --- band A: what is being watched ---
  p.push(band(t, 48, 110, W - 96, 120, "monitored workloads"));
  const svcs = [
    "meridian-gateway",
    "meridian-validation",
    "meridian-aggregation",
    "meridian-reporting",
    "demo-app",
  ];
  let cx = 76;
  svcs.forEach((s) => {
    const w = textW(s, 13, true) + 30;
    track(cx, 158, w, 38, `chip:${s}`);
    p.push(
      `<rect x="${cx}" y="158" width="${w}" height="38" rx="8" fill="${t.card}" stroke="${t.line}"/>`,
    );
    p.push(text(cx + 15, 182, s, { size: 13, mono: true, fill: t.ink2 }));
    cx += w + 14;
  });
  const promX = cx + 40;
  p.push(node(t, promX, 150, 220, 56, { title: "Prometheus", sub: "scrapes every 15s" }));
  const apiX = W - 76 - 260;
  p.push(
    node(t, apiX, 150, 260, 56, {
      title: "Kubernetes API",
      titleSize: 16,
      sub: "the only thing it can touch",
      accent: t.signal,
    }),
  );

  // --- band B: the pipeline ---
  p.push(band(t, 48, 264, W - 96, 300, "control plane \u00b7 one consumer group per stage"));
  const stages = [
    { t: "ingestion", s: "polls Prometheus", s2: "every 5s" },
    { t: "correlation", s: "median/MAD baseline", s2: "storm to one Situation" },
    { t: "rca", s: "ranks a hypothesis", s2: "picks the runbook" },
    { t: "action", s: "resolves the target", s2: "executes and verifies" },
  ];
  const bw = 268;
  const gap = 160;
  const bx0 = 76;
  stages.forEach((st, i) => {
    const x = bx0 + i * (bw + gap);
    p.push(node(t, x, 312, bw, 104, { tag: `0${i + 1}`, title: st.t, sub: st.s, sub2: st.s2 }));
  });
  ["telemetry.raw", "situations.detected", "situations.diagnosed"].forEach((tp, i) => {
    const x1 = bx0 + i * (bw + gap) + bw;
    p.push(arrow(t, x1 + 6, 364, x1 + gap - 6, 364, { label: tp, headId: H, ly: -10 }));
  });
  p.push(arrow(t, promX + 110, 212, promX + 110, 302, { headId: H }));

  // governance: the one call that blocks
  const ax = bx0 + 3 * (bw + gap);
  p.push(
    node(t, ax, 462, bw, 74, {
      title: "governance",
      titleSize: 16,
      sub: "RBAC \u00b7 reversible \u00b7 human",
      accent: t.warn,
      emphasis: true,
    }),
  );
  p.push(arrow(t, ax + bw / 2, 458, ax + bw / 2, 422, { headId: "h-warn", color: t.warn, width: 2 }));
  p.push(
    text(ax - 14, 502, "blocks", {
      size: 12,
      mono: true,
      bold: true,
      fill: t.warn,
      anchor: "end",
    }),
  );

  // action reaches the cluster
  p.push(
    `<path d="M${ax + bw / 2},306 L${ax + bw / 2},250 L${apiX + 130},250 L${apiX + 130},212" ` +
      `stroke="${t.signal}" stroke-width="1.8" fill="none" marker-end="url(#h-signal)"/>`,
  );
  p.push(
    text(ax + bw / 2 + 16, 242, "7 typed verbs, Deployment-scoped", {
      size: 12,
      mono: true,
      fill: t.signal,
    }),
  );

  // --- the consumers of the last topic, in their own column ---
  const rx = ax + bw + 180;
  p.push(
    node(t, rx, 300, W - rx - 76, 96, {
      tag: "cqrs",
      title: "read model",
      sub: "projects every stream, rebuilds on restart",
    }),
  );
  p.push(
    node(t, rx, 418, W - rx - 76, 74, {
      title: "feedback",
      titleSize: 16,
      sub: "earns or revokes autonomy",
      accent: t.ok,
    }),
  );
  p.push(
    arrow(t, ax + bw + 6, 348, rx - 6, 348, {
      label: "remediation.outcomes",
      headId: H,
      ly: -10,
    }),
  );
  p.push(
    `<path d="M${rx - 90},348 L${rx - 90},455 L${rx - 6},455" stroke="${t.ok}" ` +
      `stroke-width="1.4" fill="none" marker-end="url(#h-ok)"/>`,
  );

  // --- band C: durable state ---
  p.push(band(t, 48, 598, W - 96, 216, "durable state"));
  p.push(
    node(t, 76, 644, 800, 144, {
      tag: "redis streams",
      title: "The bus",
      sub: "telemetry.raw \u00b7 situations.detected \u00b7 situations.suppressed",
      sub2: "situations.diagnosed \u00b7 remediation.outcomes",
      subMono: true,
      accent: t.signal,
    }),
  );
  p.push(
    node(t, 912, 644, W - 912 - 76, 144, {
      tag: "postgres",
      title: "The record",
      sub: "audit_records \u00b7 training_records \u00b7 playbooks \u00b7 approvals",
      sub2: "proposed_playbooks \u00b7 agent_runs \u00b7 correlation_baseline",
      subMono: true,
      accent: t.ok,
    }),
  );

  return svg(
    t,
    W,
    860,
    "IntelliOps architecture",
    "Seven services on an event bus. One call blocks, and it is the one a human answers.",
    p.join("\n  "),
  );
}

/* ========================================================================= *
 * 2. Incident lifecycle
 * ========================================================================= */
function incidentFlow(t) {
  const H = "h-ink3";
  const p = [];
  const W = 1920;
  const y = 190;
  const bw = 252;
  const gap = 60;
  const steps = [
    { tag: "signal", title: "Alert storm", sub: "N metrics fire", sub2: "on one fault" },
    { tag: "correlate", title: "One Situation", sub: "median/MAD, z > 3.0", sub2: "held 3+ samples" },
    { tag: "diagnose", title: "Ranked cause", sub: "hypothesis + runbook", sub2: "or no match" },
    { tag: "gate", title: "Governance", sub: "RBAC, reversible", sub2: "human if not earned", accent: t.warn },
    { tag: "execute", title: "Typed action", sub: "one of 7 verbs", sub2: "Deployment-scoped", accent: t.signal },
    { tag: "verify", title: "Re-check", sub: "the metric that fired", sub2: "against its baseline", accent: t.ok },
  ];
  steps.forEach((s, i) => {
    const x = 48 + i * (bw + gap);
    p.push(node(t, x, y, bw, 116, { ...s, titleSize: 18 }));
    if (i < steps.length - 1) {
      p.push(arrow(t, x + bw + 6, y + 58, x + bw + gap - 6, y + 58, { headId: H }));
    }
  });

  // outcomes
  const ox = 48 + 5 * (bw + gap);
  p.push(
    arrow(t, ox + bw / 2, y + 122, ox + bw / 2, y + 190, { headId: "h-ok", color: t.ok }),
  );
  p.push(
    node(t, ox - 90, y + 196, bw + 120, 76, {
      title: "recovered",
      titleSize: 17,
      sub: "close, record the outcome, credit the playbook",
      accent: t.ok,
      emphasis: true,
    }),
  );
  p.push(
    arrow(t, ox - 100, y + 234, ox - 250, y + 234, {
      headId: "h-crit",
      color: t.crit,
      label: "did not hold",
      ly: -10,
    }),
  );
  p.push(
    node(t, ox - 560, y + 196, 300, 76, {
      title: "rolled back",
      titleSize: 17,
      sub: "undone automatically, playbook demoted",
      accent: t.crit,
      emphasis: true,
    }),
  );

  // the two side exits
  p.push(
    arrow(t, 48 + 2 * (bw + gap) + bw / 2, y + 122, 48 + 2 * (bw + gap) + bw / 2, y + 196, {
      headId: "h-attention",
      color: t.attention,
    }),
  );
  p.push(
    node(t, 48 + 2 * (bw + gap) - 30, y + 196, 300, 76, {
      title: "escalated",
      titleSize: 17,
      sub: "no runbook matched, a human is told",
      accent: t.attention,
      emphasis: true,
    }),
  );
  p.push(
    arrow(t, 48 + (bw + gap) + bw / 2, y + 122, 48 + (bw + gap) + bw / 2, y + 196, {
      headId: H,
    }),
  );
  p.push(
    node(t, 48 + (bw + gap) - 100, y + 196, 250, 76, {
      title: "suppressed",
      titleSize: 17,
      sub: "this signature self-heals",
    }),
  );

  p.push(
    text(48, y + 330, "Measured on a live cluster: 75% of alerts never become a page.", {
      size: 15,
      fill: t.ink2,
    }),
  );
  p.push(
    text(48, y + 356, "Fastest alert-to-verified-fix: 28.8 seconds.", { size: 15, fill: t.ink2 }),
  );

  return svg(
    t,
    W,
    640,
    "Incident lifecycle",
    "Alert storm to verified fix, with every exit the system can actually take.",
    p.join("\n  "),
  );
}

/* ========================================================================= *
 * 3. Recovery ladder  (the "what if the first fix fails" question)
 * ========================================================================= */
function recovery(t) {
  const p = [];
  const W = 1400;
  const rungs = [
    { k: "rung 1", h: "Restart the Deployment, then re-check the metric that fired", n: "built · measured", c: t.ok },
    { k: "rung 2", h: "Did not recover inside the deadline, so run the rollback path", n: "built · 2 real rollbacks", c: t.ok },
    { k: "rung 3", h: "Roll back to the last known-good revision, or scale healthy replicas", n: "built · rollback_to_revision, scale", c: t.ok },
    { k: "rung 4", h: "Escalate to a human with the full evidence trail", n: "built · needs_attention", c: t.attention },
    { k: "rung 5", h: "Shift traffic away from the failing region", n: "NOT built · Phase 2", c: t.warn },
  ];
  let y = 140;
  rungs.forEach((r, i) => {
    const w = 660 + i * 76;
    p.push(
      `<rect x="48" y="${y}" width="${w}" height="72" rx="10" fill="${t.card}" stroke="${r.c}" stroke-width="1.4"/>`,
    );
    p.push(
      text(70, y + 28, r.k.toUpperCase(), {
        size: 11,
        mono: true,
        bold: true,
        spacing: 1.2,
        fill: r.c,
      }),
    );
    p.push(text(70, y + 52, r.h, { size: 16, fill: t.ink }));
    p.push(text(w + 72, y + 44, r.n, { size: 12, mono: true, fill: t.ink3 }));
    if (i < rungs.length - 1) {
      p.push(
        arrow(t, 120, y + 74, 120, y + 96, {
          headId: "h-crit",
          color: t.crit,
        }),
      );
      p.push(text(138, y + 92, "still unhealthy", { size: 11, mono: true, fill: t.crit }));
    }
    y += 98;
  });
  p.push(
    `<rect x="48" y="${y + 14}" width="${W - 96}" height="86" rx="10" fill="${t.surface}" stroke="${t.line}"/>`,
  );
  p.push(
    text(70, y + 44, "THE HONEST LIMIT", {
      size: 11,
      mono: true,
      bold: true,
      spacing: 1.4,
      fill: t.ink3,
    }),
  );
  p.push(
    text(
      70,
      y + 70,
      "Everything through rung 4 runs today against a real cluster. Cross-region traffic shifting is not built.",
      { size: 14, fill: t.ink },
    ),
  );
  p.push(
    text(
      70,
      y + 90,
      "Every rung is a step type in the same closed vocabulary, so climbing the ladder never widens the blast radius.",
      { size: 14, fill: t.ink3 },
    ),
  );
  return svg(
    t,
    W,
    y + 132,
    "When the first fix does not work",
    "The story does not stop at remediation attempted.",
    p.join("\n  "),
  );
}

/* ========================================================================= *
 * 4. The three data sources
 * ========================================================================= */
function dataSources(t) {
  const p = [];
  const W = 1500;
  const cols = [
    {
      tag: "what happened",
      title: "Application telemetry",
      accent: t.signal,
      rows: [
        "Prometheus scrapes every service every 15s",
        "ingestion polls it every 5s and publishes",
        "one typed event per series",
      ],
      store: "redis: telemetry.raw",
    },
    {
      tag: "what was decided",
      title: "Audit records",
      accent: t.warn,
      rows: [
        "Every gate evaluation, approval, rejection,",
        "execution and outcome. Threaded by",
        "correlation_id. Append-only.",
      ],
      store: "postgres: audit_records",
    },
    {
      tag: "what we learned",
      title: "Historical incidents",
      accent: t.ok,
      rows: [
        "Every outcome with its signature, playbook",
        "and whether it worked. Drives reliability,",
        "graduation to autonomy, and suppression.",
      ],
      store: "postgres: training_records",
    },
  ];
  const cw = (W - 96 - 2 * 28) / 3;
  cols.forEach((c, i) => {
    const x = 48 + i * (cw + 28);
    p.push(
      `<rect x="${x}" y="140" width="${cw}" height="270" rx="12" fill="${t.card}" stroke="${c.accent}" stroke-width="1.4"/>`,
    );
    p.push(
      text(x + 22, 172, c.tag.toUpperCase(), {
        size: 11,
        mono: true,
        bold: true,
        spacing: 1.3,
        fill: c.accent,
      }),
    );
    p.push(text(x + 22, 206, c.title, { size: 21, bold: true, fill: t.ink }));
    c.rows.forEach((r, j) => {
      p.push(text(x + 22, 244 + j * 24, r, { size: 13.5, fill: t.ink2 }));
    });
    p.push(
      `<rect x="${x + 22}" y="336" width="${cw - 44}" height="42" rx="8" fill="${t.surface}" stroke="${t.line}"/>`,
    );
    p.push(text(x + 38, 362, c.store, { size: 13, mono: true, fill: c.accent }));
  });
  p.push(
    text(
      48,
      452,
      "Also durable: playbooks, proposed_playbooks, approvals, agent_runs, agent_run_steps, correlation_baseline, model_artifacts.",
      { size: 13, mono: true, fill: t.ink3 },
    ),
  );
  return svg(
    t,
    W,
    500,
    "Three data sources, and where each one lives",
    "Not one blob called history. Three stores, each answering a different question.",
    p.join("\n  "),
  );
}

/* ========================================================================= *
 * 5. Earned autonomy
 * ========================================================================= */
function autonomy(t) {
  const p = [];
  const W = 1300;
  p.push(
    node(t, 120, 190, 330, 120, {
      tag: "start here",
      title: "hitl",
      titleSize: 30,
      sub: "every run waits for a human",
      accent: t.warn,
      emphasis: true,
    }),
  );
  p.push(
    node(t, 850, 190, 330, 120, {
      tag: "earned",
      title: "auto",
      titleSize: 30,
      sub: "runs unattended",
      accent: t.ok,
      emphasis: true,
    }),
  );
  p.push(
    arrow(t, 458, 226, 842, 226, {
      headId: "h-ok",
      color: t.ok,
      width: 2,
      label: "3 successes · 0 failures · 0 rollbacks",
      ly: -14,
    }),
  );
  p.push(
    arrow(t, 842, 286, 458, 286, {
      headId: "h-crit",
      color: t.crit,
      width: 2,
      label: "any failure or rollback",
      ly: 26,
    }),
  );
  p.push(
    `<rect x="120" y="374" width="${W - 240}" height="116" rx="12" fill="${t.card}" stroke="${t.line}"/>`,
  );
  p.push(
    text(146, 406, "WHY THIS IS THE ARGUMENT", {
      size: 11,
      mono: true,
      bold: true,
      spacing: 1.4,
      fill: t.ink3,
    }),
  );
  p.push(
    text(
      146,
      436,
      "Autonomy is not a setting. A playbook starts human-gated and reaches unattended execution only by being right,",
      { size: 15, fill: t.ink },
    ),
  );
  p.push(
    text(
      146,
      462,
      "and it loses that the moment it is wrong. Our run started at 0% autonomous and reached 76.7%.",
      { size: 15, fill: t.ink },
    ),
  );
  return svg(
    t,
    W,
    540,
    "Autonomy is earned, not configured",
    "ADR-008. The state a playbook is in is a fact about its track record.",
    p.join("\n  "),
  );
}

/* ========================================================================= *
 * 6. The whole project, as one flowchart
 * ========================================================================= */
function projectFlow(t) {
  const p = [];
  const W = 1920;
  const spineY = 196;
  const bw = 300;
  const bh = 116;
  const step = 372;
  const xs = [80, 452, 824, 1196, 1568];

  const spine = [
    { title: "Alert storm", sub: "8 metrics fire", sub2: "on one fault", accent: t.crit },
    { title: "Correlate", sub: "median/MAD baseline", sub2: "one Situation" },
    { title: "Diagnose", sub: "rank the cause", sub2: "pick a runbook" },
    { title: "Gate", sub: "RBAC, reversible", sub2: "human if not earned", accent: t.warn },
    { title: "Execute and verify", sub: "1 of 7 typed verbs", sub2: "re-check the metric", accent: t.signal },
  ];
  spine.forEach((sp, i) => {
    p.push(node(t, xs[i], spineY, bw, bh, { ...sp, titleSize: 19 }));
    if (i < spine.length - 1) {
      p.push(
        arrow(t, xs[i] + bw + 6, spineY + bh / 2, xs[i + 1] - 6, spineY + bh / 2, {
          headId: "h-ink3",
        }),
      );
    }
  });

  // Each branch: a diamond under the step, then where that answer lands.
  const dy = 394;
  const oy = 512;
  const branches = [
    { i: 1, q: "self-heals?", qs: "from outcomes", ans: "yes", tone: "ink3",
      title: "Suppressed", sub: "no page raised", accent: null },
    { i: 2, q: "runbook fits?", qs: "confidence floor", ans: "no", tone: "attention",
      title: "Escalated", sub: "a human is told, nothing is guessed", accent: t.attention },
    { i: 3, q: "approved?", qs: "or timed out", ans: "no", tone: "warn",
      title: "No action", sub: "refusal recorded in the audit trail", accent: t.warn },
  ];
  branches.forEach((b) => {
    const cx = xs[b.i] + bw / 2;
    p.push(arrow(t, cx, spineY + bh + 6, cx, dy - 40, { headId: "h-ink3" }));
    p.push(diamond(t, cx, dy, 210, 74, b.q, b.qs));
    p.push(
      arrow(t, cx, dy + 40, cx, oy - 6, {
        headId: b.tone === "ink3" ? "h-ink3" : `h-${b.tone}`,
        color: b.tone === "ink3" ? t.ink3 : t[b.tone],
        label: b.ans,
        ly: 4,
      }),
    );
    p.push(
      node(t, xs[b.i] - 10, oy, bw + 20, 92, {
        title: b.title,
        titleSize: 17,
        sub: b.sub,
        accent: b.accent,
      }),
    );
  });

  // The last step has two answers, so it gets two outcome boxes.
  const lx = xs[4] + bw / 2;
  p.push(arrow(t, lx, spineY + bh + 6, lx, dy - 40, { headId: "h-ink3" }));
  p.push(diamond(t, lx, dy, 210, 74, "recovered?", "vs its baseline"));
  p.push(
    arrow(t, lx, dy + 40, lx, oy - 6, { headId: "h-ok", color: t.ok, label: "yes", ly: 4 }),
  );
  p.push(
    node(t, xs[4] - 10, oy, bw + 20, 92, {
      title: "Closed",
      titleSize: 17,
      sub: "verified healthy, playbook credited",
      accent: t.ok,
    }),
  );
  const gut = xs[4] - 46; // the clear gutter between "No action" and "Closed"
  p.push(
    `<path d="M${lx - 105},${dy} L${gut},${dy} L${gut},672 L${xs[4] - 16},672" ` +
      `stroke="${t.crit}" stroke-width="1.4" fill="none" marker-end="url(#h-crit)"/>`,
  );
  p.push(text(lx - 118, dy - 10, "no", { size: 11, mono: true, fill: t.crit, anchor: "end" }));
  p.push(
    node(t, xs[4] - 10, 626, bw + 20, 92, {
      title: "Rolled back",
      titleSize: 17,
      sub: "undone automatically, then escalated",
      accent: t.crit,
    }),
  );

  p.push(
    text(80, 762, "Every box is code that runs. Nothing on this page is a plan.", {
      size: 15,
      fill: t.ink2,
    }),
  );
  p.push(
    text(
      80,
      788,
      "Measured on a live cluster: 75% of alerts never become a page, and the fastest alert-to-verified-fix was 28.8 seconds.",
      { size: 15, fill: t.ink3 },
    ),
  );

  return svg(
    t,
    W,
    830,
    "How IntelliOps handles one incident",
    "Five steps, four decisions, and every exit the system can take.",
    p.join("\n  "),
  );
}

/* ------------------------------------------------------------------------- */
const DIAGRAMS = {
  "01-architecture": architecture,
  "02-incident-flow": incidentFlow,
  "03-recovery-ladder": recovery,
  "04-data-sources": dataSources,
  "05-earned-autonomy": autonomy,
  "06-project-flowchart": projectFlow,
};

mkdirSync(OUT, { recursive: true });
let n = 0;
let ok = true;
for (const [name, fn] of Object.entries(DIAGRAMS)) {
  for (const [theme, t] of Object.entries(THEMES)) {
    BOXES = [];
    const out = fn(t);
    const m = out.match(/viewBox="0 0 (\d+) (\d+)"/);
    if (theme === "dark" && !validate(name, Number(m[1]), Number(m[2]))) ok = false;
    writeFileSync(join(OUT, `${name}.${theme}.svg`), out, "utf8");
    n++;
  }
}
console.log(`wrote ${n} diagrams${ok ? "" : " WITH GEOMETRY ERRORS"}`);
process.exit(ok ? 0 : 1);
