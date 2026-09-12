/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Meridian's own palette — light enterprise-fintech. Deliberately
        // NOT the console's dark instrument-panel + cyan: white/off-white
        // surfaces, near-black ink, and an emerald brand accent (not cyan).
        surface: { DEFAULT: "#FFFFFF", subtle: "#F7F8FA", sunken: "#EEF1F4" },
        ink: { DEFAULT: "#0B1220", 2: "#3D4759", 3: "#6B7686", 4: "#A6AFBB" },
        line: { DEFAULT: "#E2E6EB", strong: "#CBD2DA" },
        brand: {
          DEFAULT: "#0E7C5A",
          dim: "#0B6248",
          tint: "#E4F3ED",
          navy: "#1B2A4A",
          navyTint: "#E9ECF3",
        },
        data: {
          pos: "#0E7C5A",
          neg: "#B3261E",
          warn: "#B3760E",
          info: "#1B2A4A",
        },
      },
      fontFamily: {
        // Humanist/grotesk sans for UI, serif for report headings — the
        // opposite pairing from the console's Geist.
        sans: [
          "'Public Sans'",
          "'IBM Plex Sans'",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        serif: [
          "'Source Serif 4'",
          "'Georgia'",
          "ui-serif",
          "Cambria",
          "serif",
        ],
        mono: ["'IBM Plex Mono'", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.01em" }],
      },
      borderRadius: {
        sm: "0.25rem",
        DEFAULT: "0.375rem",
        lg: "0.5rem",
      },
      boxShadow: {
        card: "0 1px 2px rgba(11,18,32,0.04), 0 1px 1px rgba(11,18,32,0.03)",
        raised: "0 4px 16px -4px rgba(11,18,32,0.10), 0 1px 2px rgba(11,18,32,0.05)",
      },
      keyframes: {
        pulseDot: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
      },
      animation: {
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
