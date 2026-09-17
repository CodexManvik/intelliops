/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Neutral near-black, in the Vercel/Geist manner: no hue at all, so the
        // one accent is the only colour on the page that isn't data. The first
        // pass tinted these blue, which read as murk rather than as a surface.
        ground: { DEFAULT: "#0A0A0A", raised: "#111111", sunken: "#000000" },
        // Pure-neutral greys. ink-3 carries most of the 11px operational
        // detail, so it is set to clear WCAG AA on `raised` (5.8:1) rather than
        // chosen by eye.
        ink: { DEFAULT: "#EDEDED", 2: "#A1A1A1", 3: "#8F8F8F", 4: "#7D7D7D" },
        // Hairlines. These are deliberately VISIBLE: a 1px #2E2E2E rule doing
        // the work is what separates a crisp dark UI from a soft one, and it
        // removes the need for shadows to imply an edge.
        line: { DEFAULT: "#242424", strong: "#2E2E2E" },
        // One accent, used for text, rules, focus and the primary chart series.
        // Lifted to 7.5:1 on `raised` so it never needs a lighter variant.
        signal: { DEFAULT: "#52A8FF", dim: "#0072F5", glow: "rgba(82,168,255,0.14)" },
        // Five severity tones, all AA on `raised`. `attention` stays distinct
        // from warn and crit: "stopped, a human is needed" must not read as
        // "currently remediating" or "the fix failed".
        sev: {
          ok: "#62C073",
          warn: "#FFB224",
          crit: "#FF6369",
          info: "#8F8FF5",
          attention: "#BF7AF0",
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
