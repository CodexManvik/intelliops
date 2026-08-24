type Tone = "ok" | "warn" | "crit" | "neutral";

const CLASS_BY_TONE: Record<Tone, string> = {
  ok: "pill-ok",
  warn: "pill-warn",
  crit: "pill-crit",
  neutral: "pill-neutral",
};

const DOT_BY_TONE: Record<Tone, string> = {
  ok: "bg-brand",
  warn: "bg-data-warn",
  crit: "bg-data-neg",
  neutral: "bg-ink-4",
};

export default function StatusPill({
  tone,
  children,
  pulse = false,
}: {
  tone: Tone;
  children: React.ReactNode;
  pulse?: boolean;
}) {
  return (
    <span className={CLASS_BY_TONE[tone]}>
      <span
        className={`h-1.5 w-1.5 rounded-full ${DOT_BY_TONE[tone]} ${pulse ? "animate-pulseDot" : ""}`}
      />
      {children}
    </span>
  );
}
