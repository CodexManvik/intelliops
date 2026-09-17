/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // The theme is stamped on <html> as data-theme by the pre-paint script in
  // index.html; nothing in the app uses Tailwind's own `dark:` variant.
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Every colour resolves through a CSS custom property holding RGB
        // channels, so the same class works in both themes and Tailwind's
        // opacity modifiers still compose (`bg-signal/12`, `border-line/40`).
        // The values live in src/styles/index.css; this file only names them.
        ground: {
          DEFAULT: "rgb(var(--ground) / <alpha-value>)",
          raised: "rgb(var(--ground-raised) / <alpha-value>)",
          sunken: "rgb(var(--ground-sunken) / <alpha-value>)",
        },
        // A subtle step up from the card, for nested blocks. Replaces the
        // `bg-white/[0.0x]` washes, which are invisible on a light ground.
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          2: "rgb(var(--surface-2) / <alpha-value>)",
          3: "rgb(var(--surface-3) / <alpha-value>)",
        },
        // ink-3 carries most of the 11px operational detail, so each tone is
        // set to clear WCAG AA on `raised` in BOTH themes rather than chosen
        // by eye. scripts/check-contrast.mjs enforces it.
        ink: {
          DEFAULT: "rgb(var(--ink) / <alpha-value>)",
          2: "rgb(var(--ink-2) / <alpha-value>)",
          3: "rgb(var(--ink-3) / <alpha-value>)",
          4: "rgb(var(--ink-4) / <alpha-value>)",
        },
        // Hairlines. Deliberately visible: a 1px rule doing the work is what
        // separates a crisp UI from a soft one, and it removes the need for
        // shadows to imply an edge.
        line: {
          DEFAULT: "rgb(var(--line) / <alpha-value>)",
          strong: "rgb(var(--line-strong) / <alpha-value>)",
        },
        // One accent, used for text, rules, focus and the primary chart series.
        signal: {
          DEFAULT: "rgb(var(--signal) / <alpha-value>)",
          dim: "rgb(var(--signal-dim) / <alpha-value>)",
          glow: "rgb(var(--signal) / 0.14)",
        },
        // Five severity tones. `attention` stays distinct from warn and crit:
        // "stopped, a human is needed" must not read as "currently
        // remediating" or "the fix failed".
        sev: {
          ok: "rgb(var(--sev-ok) / <alpha-value>)",
          warn: "rgb(var(--sev-warn) / <alpha-value>)",
          crit: "rgb(var(--sev-crit) / <alpha-value>)",
          info: "rgb(var(--sev-info) / <alpha-value>)",
          attention: "rgb(var(--sev-attention) / <alpha-value>)",
        },
      },
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.02em" }],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      borderRadius: {
        "4xl": "2rem",
        "5xl": "2.5rem",
      },
      transitionTimingFunction: {
        // Apple's fluid curve + a heavier settle for large moves
        fluid: "cubic-bezier(0.32, 0.72, 0, 1)",
        spring: "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      boxShadow: {
        // Borders carry the edges here, so shadows only need to say "this sits
        // slightly above the page". The previous values were tuned for a white
        // background and were simply invisible on a dark one.
        lift: "0 1px 2px rgba(0,0,0,0.45)",
        glow: "0 0 0 1px rgba(82,168,255,0.45), 0 0 24px -6px rgba(82,168,255,0.25)",
        inset: "inset 0 1px 0 rgba(255,255,255,0.035)",
      },
      keyframes: {
        beat: {
          "0%,100%": { transform: "scale(1)", opacity: "1" },
          "50%": { transform: "scale(0.7)", opacity: "0.6" },
        },
        sweep: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        beat: "beat 2.2s cubic-bezier(0.32,0.72,0,1) infinite",
        sweep: "sweep 2.4s cubic-bezier(0.32,0.72,0,1) infinite",
      },
    },
  },
  plugins: [],
};
