/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        ground: { DEFAULT: "#FFFFFF", raised: "#FFFFFF", sunken: "#F5F5F7" },
        ink: { DEFAULT: "#1D1D1F", 2: "#6E6E73", 3: "#86868B", 4: "#C7C7CC" },
        signal: { DEFAULT: "#0071E3", dim: "#0058B0", glow: "rgba(0,113,227,0.14)" },
        sev: { ok: "#34C759", warn: "#FF9500", crit: "#FF3B30", info: "#5E5CE6" },
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
        lift: "0 1px 2px rgba(0,0,0,0.04), 0 12px 32px -12px rgba(0,0,0,0.12)",
        glow: "0 0 0 1px rgba(0,113,227,0.35), 0 8px 24px -8px rgba(0,113,227,0.20)",
        inset: "inset 0 1px 0 rgba(255,255,255,0.6), inset 0 0 0 1px rgba(0,0,0,0.04)",
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
