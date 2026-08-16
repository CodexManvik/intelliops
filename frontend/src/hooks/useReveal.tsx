import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Scroll-reveal via IntersectionObserver + CSS (`.reveal-css` / `.in`).
 * Deliberately not Framer's whileInView: under React StrictMode the JS-driven
 * mount animation can strand at its initial (invisible) state. CSS transitions
 * always resolve, and the observer just flips a class.
 */
export function Reveal({
  children,
  className = "",
  delay = 0,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // Reveal-on-scroll when the page is actually being composited.
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShown(true);
            io.disconnect();
          }
        }
      },
      { threshold: 0.14 },
    );
    io.observe(el);

    // Safety net: never gate content behind an observer/transition that might
    // not run (a hidden/backgrounded tab throttles both). If the page is not
    // visible, reveal immediately; otherwise reveal after a short grace period
    // in case the observer never fires for on-screen content.
    const immediate = typeof document !== "undefined" && document.visibilityState !== "visible";
    const fallback = window.setTimeout(() => setShown(true), immediate ? 0 : 1200);

    return () => {
      io.disconnect();
      window.clearTimeout(fallback);
    };
  }, []);

  return (
    <div
      ref={ref}
      className={`reveal-css ${shown ? "in" : ""} ${className}`}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}
