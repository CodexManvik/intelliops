/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkmode: "class",
  theme: {
    extend: {
      colors: {
        // ground: deep instrument-panel graphite, blue-biased (not pure black)
        ground: {
          DEFAULT: "#080B10",
          raised: "#0C111A",
          sunken: "#05070B",
        },
        // hairlines & surfaces expressed as tokens (used with /opacity too)
        ink: {
          DEFAULT: "#EAF0F7",
          2: "#A6B4C6",
          3: "#66748A",
          4: "#3C4757",
        },
        // accent: signal cyan — reserved for live / resolved / focus
        signal: {
          DEFAULT: "#3DD6D0",
          dim: "#2AA9A4",
          glow: "rgba(61,214,208,0.24)",
        },
        // semantic severity — distinct from accent
        sev: {
          ok: "#43D18A",
          warn: "#F5A623",
          crit: "#F26D6D",
          info: "#6E8BFF",
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
        // soft, diffused — never harsh
        lift: "0 1px 2px rgba(0,0,0,0.35), 0 18px 50px -12px rgba(0,0,0,0.5)",
        glow: "0 0 0 1px rgba(61,214,208,0.35), 0 8px 30px rgba(61,214,208,0.18)",
        inset: "inset 0 1px 1px rgba(255,255,255,0.06)",
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
