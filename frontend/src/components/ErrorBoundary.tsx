import { Component, type ErrorInfo, type ReactNode } from "react";

/* ---------------------------------------------------------------------------
   A render error in ONE view must not take the whole console down.

   There was no boundary anywhere in the app, so any uncaught error unmounted
   the entire React tree and left a blank white page with no way back. That is
   exactly what clicking "Draft a runbook with AI" did: AgentActivity opened an
   EventSource from an effect, the URL construction threw, and the operator lost
   the console mid-incident with nothing on screen to explain it.

   The URL bug itself is fixed in data/api.ts. This is the second line of
   defence: whatever else throws later, the surrounding shell (navigation,
   header, the other views) survives and the operator can keep working.
--------------------------------------------------------------------------- */

type Props = {
  children: ReactNode;
  /** Remounts the boundary when it changes, so switching view clears an error. */
  resetKey?: unknown;
};

type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    // Navigating away from a broken view should offer a working console again.
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the real stack in the console: the blank page was undiagnosable
    // precisely because nothing surfaced the cause.
    console.error("view crashed:", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="rounded-2xl border border-sev-crit/30 bg-sev-crit/5 p-6">
        <p className="text-sm font-semibold text-sev-crit">This panel failed to render.</p>
        <p className="mt-2 text-sm text-ink-dim">
          The rest of the console still works — switch to another view and back, or reload.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg bg-black/30 p-3 text-xs text-ink-dim">
          {error.message}
        </pre>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="mt-4 rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-white/5"
        >
          Try again
        </button>
      </div>
    );
  }
}
