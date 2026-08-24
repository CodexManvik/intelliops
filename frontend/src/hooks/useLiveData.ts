import { useEffect, useState } from "react";
import { openStream } from "../data/api";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

export function useLiveData<T>(loader: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      loader()
        .then((d) => alive && (setData(d), setError(null)))
        .catch((e) => alive && setError(String(e)))
        .finally(() => alive && setLoading(false));

    tick(); // initial load in every mode

    if (!LIVE) return () => { alive = false; }; // mock mode: one load, no stream/poll

    let pollId: number | undefined;
    const startPoll = () => {
      if (pollId === undefined) pollId = window.setInterval(tick, 5000);
    };

    let es: EventSource | null = null;
    try {
      es = openStream();
      es.onmessage = () => tick();      // {"type":"changed"} nudge → refetch
      es.onerror = () => startPoll();   // EventSource auto-reconnects; poll covers hard failures
    } catch {
      startPoll();
    }

    return () => {
      alive = false;
      es?.close();                       // StrictMode double-mount safety
      if (pollId !== undefined) window.clearInterval(pollId);
    };
  }, [loader]);

  return { data, loading, error };
}
