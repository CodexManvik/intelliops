import { useCallback, useEffect, useState } from "react";

/* ---------------------------------------------------------------------------
   Theme preference.

   Three states, not two. "system" is the default and it is the one most people
   actually want: an operator who has their OS on a schedule should not have to
   flip the console separately at dusk. A binary toggle cannot express that, and
   silently pinning a theme on first visit is the thing that makes people go
   looking for a setting.

   The resolved value is stamped on <html data-theme>, which is what every
   colour in src/styles/index.css keys off. A matching pre-paint script in
   index.html does the same thing before React mounts, so there is no flash of
   the wrong theme on load. The two must stay in agreement: same storage key,
   same resolution rule.
--------------------------------------------------------------------------- */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_KEY = "intelliops.theme";

const QUERY = "(prefers-color-scheme: light)";

function systemTheme(): ResolvedTheme {
  return typeof window !== "undefined" && window.matchMedia(QUERY).matches ? "light" : "dark";
}

function readPreference(): ThemePreference {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    // Private mode, or storage blocked. Falling back to "system" is correct:
    // the console still renders, it just will not remember the choice.
  }
  return "system";
}

function apply(resolved: ResolvedTheme) {
  document.documentElement.setAttribute("data-theme", resolved);
}

export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(readPreference);
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    preference === "system" ? systemTheme() : preference,
  );

  // Re-resolve whenever the preference changes, and keep following the OS while
  // the preference is "system".
  useEffect(() => {
    const next = preference === "system" ? systemTheme() : preference;
    setResolved(next);
    apply(next);

    if (preference !== "system") return;
    const mq = window.matchMedia(QUERY);
    const onChange = () => {
      const r = systemTheme();
      setResolved(r);
      apply(r);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [preference]);

  const setPreference = useCallback((p: ThemePreference) => {
    setPreferenceState(p);
    try {
      window.localStorage.setItem(THEME_KEY, p);
    } catch {
      // See readPreference: an unwritable store costs persistence, not function.
    }
  }, []);

  return { preference, resolved, setPreference };
}
