import { useState, type FormEvent } from "react";
import { submitData, type SubmissionResult } from "../data/api";
import { seededClients } from "../data/mock";

const PERIODS = ["FY26-Q2", "FY26-Q1", "FY25-Q4"];

type SubmitState =
  | { phase: "idle" }
  | { phase: "submitting" }
  | { phase: "success"; result: SubmissionResult }
  | { phase: "error"; message: string };

export default function Submit() {
  const [client, setClient] = useState(seededClients[0]);
  const [period, setPeriod] = useState(PERIODS[0]);
  const [amount, setAmount] = useState("125000");
  const [notes, setNotes] = useState("");
  const [state, setState] = useState<SubmitState>({ phase: "idle" });

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const parsedAmount = Number.parseFloat(amount);
    if (!client.trim() || !period.trim() || Number.isNaN(parsedAmount)) {
      setState({ phase: "error", message: "Please complete all required fields." });
      return;
    }
    setState({ phase: "submitting" });
    try {
      const result = await submitData({ client, period, amount: parsedAmount });
      setState({ phase: "success", result });
    } catch (err) {
      setState({
        phase: "error",
        message: err instanceof Error ? err.message : "Submission failed.",
      });
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-ink">Submit financials</h1>
        <p className="mt-1 text-sm text-ink-3">
          Submit a client&rsquo;s figures for the current reporting period. Submissions are
          validated and routed through aggregation before appearing in Reports.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="card space-y-5 p-6">
        <div>
          <label className="label" htmlFor="client">
            Client
          </label>
          <select
            id="client"
            className="input"
            value={client}
            onChange={(e) => setClient(e.target.value)}
          >
            {seededClients.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label" htmlFor="period">
              Reporting period
            </label>
            <select
              id="period"
              className="input"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            >
              {PERIODS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="amount">
              Amount (USD)
            </label>
            <input
              id="amount"
              className="input"
              type="number"
              min="0"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
          </div>
        </div>

        <div>
          <label className="label" htmlFor="notes">
            Notes <span className="normal-case text-ink-4">(optional)</span>
          </label>
          <textarea
            id="notes"
            className="input min-h-20 resize-y"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Context for the reviewer, adjustments made, etc."
          />
        </div>

        <div className="flex items-center gap-3 border-t border-line pt-5">
          <button type="submit" className="btn-primary" disabled={state.phase === "submitting"}>
            {state.phase === "submitting" ? "Submitting…" : "Submit for review"}
          </button>
          {state.phase === "success" && (
            <span className="text-sm font-medium text-brand-dim">
              Accepted — {state.result.client} · {state.result.period} · $
              {state.result.amount.toLocaleString()}
            </span>
          )}
          {state.phase === "error" && (
            <span className="text-sm font-medium text-data-neg">{state.message}</span>
          )}
        </div>
      </form>

      <div className="card p-5 text-sm text-ink-3">
        <div className="mb-1 font-semibold text-ink-2">What happens next</div>
        Submissions post to the gateway&rsquo;s <code className="font-mono text-xs">/api/submissions</code>{" "}
        endpoint, which routes through validation and aggregation before a report can be
        generated for the period.
      </div>
    </div>
  );
}
