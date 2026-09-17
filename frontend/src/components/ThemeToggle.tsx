import { Desktop, Moon, Sun } from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { useTheme, type ThemePreference } from "../hooks/useTheme";

/* ---------------------------------------------------------------------------
   Theme control.

   Deliberately not a sun/moon switch. A binary switch cannot express "follow
   the machine", which is the setting most people want and the only one that
   behaves correctly when the OS is on a schedule. Three segments, with the
   active one carrying a sliding indicator so the change reads as a move rather
   than a repaint.

   Icon-only, with real labels for screen readers and a title for the mouse,
   because the nav has no room for three words and the icons are unambiguous.
--------------------------------------------------------------------------- */

const OPTIONS: { id: ThemePreference; label: string; icon: JSX.Element }[] = [
  { id: "system", label: "Follow system", icon: <Desktop size={14} weight="regular" /> },
  { id: "light", label: "Light", icon: <Sun size={14} weight="regular" /> },
  { id: "dark", label: "Dark", icon: <Moon size={14} weight="regular" /> },
];

export function ThemeToggle() {
  const { preference, resolved, setPreference } = useTheme();

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="flex items-center gap-0.5 rounded-lg border border-line p-0.5"
    >
      {OPTIONS.map((o) => {
        const active = preference === o.id;
        return (
          <button
            key={o.id}
            role="radio"
            aria-checked={active}
            aria-label={
              o.id === "system" ? `Follow system, currently ${resolved}` : o.label
            }
            title={o.id === "system" ? `Follow system (${resolved})` : o.label}
            onClick={() => setPreference(o.id)}
            className={`relative flex h-6 w-7 items-center justify-center rounded-md transition-colors duration-200 ${
              active ? "text-ink" : "text-ink-4 hover:text-ink-2"
            }`}
          >
            {active && (
              <motion.span
                layoutId="themepill"
                className="absolute inset-0 rounded-md bg-surface-3"
                transition={{ type: "spring", stiffness: 420, damping: 34 }}
              />
            )}
            <span className="relative">{o.icon}</span>
          </button>
        );
      })}
    </div>
  );
}
