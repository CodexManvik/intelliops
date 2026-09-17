/* WCAG AA contrast gate for both themes.
 *
 * The palette lives in src/styles/index.css as RGB channel triples. This reads
 * them straight out of that file rather than duplicating the values, so the
 * check cannot drift from what actually ships.
 *
 * Run: node scripts/check-contrast.mjs
 * Exits non-zero on any failure, so it can go in CI.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "src", "styles", "index.css"), "utf8");

/** Pull the `--token: r g b;` declarations out of one :root block. */
function readTheme(selector) {
  const i = css.indexOf(selector);
  if (i === -1) throw new Error(`no block for ${selector}`);
  const open = css.indexOf("{", i);
  const close = css.indexOf("\n  }", open);
  const body = css.slice(open, close);
  const out = {};
  for (const m of body.matchAll(/--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}

function luminance([r, g, b]) {
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fg, bg) {
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

const hex = (c) => "#" + c.map((n) => n.toString(16).padStart(2, "0")).join("");

// Foreground tones that carry real text, and the surfaces text sits on. The
// surface tokens are included because nested blocks (`bg-surface-2`) are a
// different background from the card, and a tone that passes on the card can
// still fail inside one.
const FOREGROUNDS = [
  "ink",
  "ink-2",
  "ink-3",
  "ink-4",
  "signal",
  "sev-ok",
  "sev-warn",
  "sev-crit",
  "sev-info",
  "sev-attention",
];
const BACKGROUNDS = ["ground", "ground-raised", "surface", "surface-2", "surface-3"];

const AA = 4.5;
let failures = 0;
let checks = 0;

for (const [name, selector] of [
  ["dark", ':root[data-theme="dark"]'],
  ["light", ':root[data-theme="light"]'],
]) {
  const t = readTheme(selector);
  const rows = [];
  for (const f of FOREGROUNDS) {
    for (const b of BACKGROUNDS) {
      if (!t[f] || !t[b]) throw new Error(`${name}: missing token ${!t[f] ? f : b}`);
      const ratio = contrast(t[f], t[b]);
      checks++;
      if (ratio < AA) {
        failures++;
        rows.push(
          `  FAIL ${f} ${hex(t[f])} on ${b} ${hex(t[b])} = ${ratio.toFixed(2)}:1 (need ${AA})`,
        );
      }
    }
  }
  console.log(`${name}: ${rows.length === 0 ? "all pass" : `${rows.length} failing`}`);
  rows.forEach((r) => console.log(r));
}

// Chart series are strokes rather than text, so AA text ratios do not apply,
// but a line the eye cannot separate from the panel is still a broken chart.
// 3:1 is the WCAG non-text contrast minimum and the right bar here.
const NON_TEXT = 3.0;
for (const [name, selector] of [
  ["dark", ':root[data-theme="dark"]'],
  ["light", ':root[data-theme="light"]'],
]) {
  const t = readTheme(selector);
  const bad = [];
  for (let i = 1; i <= 6; i++) {
    const key = `chart-${i}`;
    const ratio = contrast(t[key], t["ground-raised"]);
    checks++;
    if (ratio < NON_TEXT) {
      failures++;
      bad.push(`  FAIL ${key} ${hex(t[key])} on card = ${ratio.toFixed(2)}:1 (need ${NON_TEXT})`);
    }
  }
  console.log(`${name} chart series: ${bad.length === 0 ? "all pass" : `${bad.length} failing`}`);
  bad.forEach((r) => console.log(r));
}

console.log(`\n${checks - failures}/${checks} pass`);
process.exit(failures === 0 ? 0 : 1);
