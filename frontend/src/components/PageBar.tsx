import { ArrowLeft } from "@phosphor-icons/react";
import { ThemeToggle } from "./ThemeToggle";

export type PageId = "dashboard" | "audit-log" | "product" | "docs";

/* `sample` marks a page whose numbers and names are illustrative, not read from
   the running stack. The audit log is the real governance trail, so it is not. */
export const PAGE_LINKS: { id: PageId; label: string; sample: boolean }[] = [
  { id: "product", label: "Product", sample: true },
  { id: "docs", label: "Docs", sample: true },
  { id: "dashboard", label: "Demo dashboard", sample: true },
  { id: "audit-log", label: "Audit log", sample: false },
];

/* Fixed 60px bar above the standalone pages: the way back to the console, the
   way between pages, and the theme toggle. The pages reserve this height. */
export function PageBar({ current }: { current: PageId }) {
  const sample = PAGE_LINKS.find((p) => p.id === current)?.sample;
  return (
    <header className="fixed inset-x-0 top-0 z-40 flex h-[60px] items-center gap-4 border-b border-line bg-ground/90 px-4 backdrop-blur-xl sm:px-6">
      <a
        href="#/overview"
        className="flex shrink-0 items-center gap-1.5 text-[13px] font-medium text-ink-2 transition-colors hover:text-ink"
      >
        <ArrowLeft size={14} />
        Console
      </a>
      <nav aria-label="Pages" className="flex min-w-0 items-center gap-1 overflow-x-auto border-l border-line-strong pl-3">
        {PAGE_LINKS.map((p) => (
          <a
            key={p.id}
            href={`#/${p.id}`}
            aria-current={p.id === current ? "page" : undefined}
            className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-[13px] transition-colors ${
              p.id === current ? "bg-surface-2 text-ink" : "text-ink-3 hover:text-ink-2"
            }`}
          >
            {p.label}
          </a>
        ))}
      </nav>
      <div className="ml-auto flex shrink-0 items-center gap-3">
        {sample && (
          <span
            title="This page shows illustrative data, not the running stack."
            className="hidden rounded-full border border-sev-warn/30 bg-sev-warn/10 px-2.5 py-0.5 font-mono text-2xs text-sev-warn sm:inline"
          >
            sample data
          </span>
        )}
        <ThemeToggle />
      </div>
    </header>
  );
}
