import { useEffect, useRef, useState } from "react";
import { openStream } from "../data/api";

const LIVE = import.meta.env.VITE_DATA_MODE === "live";

/* ---------------------------------------------------------------------------
   ONE shared EventSource for the whole app.

   Each useLiveData used to open its own. A view mounts three or four of these,
   and a browser allows only ~6 concurrent HTTP/1.1 connections per origin — so
   the later panels' streams sat permanently in CONNECTING and those panels
   never refreshed, while the header still advertised "streaming".

   One connection, many subscribers, reference-counted so it closes when the
   last consumer unmounts.
--------------------------------------------------------------------------- */

type Sub = () => void;

let es: EventSource | null = null;
let subs = new Set<Sub>();
let streamHealthy = false;

function ensureStream() {
  if (es || !LIVE) return;
  try {
    es = openStream();
    es.onopen = () => {
      streamHealthy = true;
    };
    es.onmessage = () => {
      streamHealthy = true;
      subs.forEach((fn) => fn());
    };
    es.onerror = () => {
      // EventSource reconnects on its own; the poll below is the real backstop.
      streamHealthy = false;
    };
  } catch {
    es = null;
    streamHealthy = false;
  }
}

function subscribe(fn: Sub): () => void {
  subs.add(fn);
  ensureStream();
  return () => {
    subs.delete(fn);
    if (subs.size === 0) {
      es?.close();
      es = null;
      streamHealthy = false;
    }
  };
}

/** True when the shared stream is currently connected. For honest UI badges. */
export function isStreamHealthy(): boolean {
  return streamHealthy;
}

// The stream only nudges on situation-lifecycle transitions, so it can be silent
// for minutes on a quiet fleet while metrics still move. We therefore ALWAYS
// poll — the stream just makes updates feel instant when something happens.
const POLL_MS = 5000;

export function useLiveData<T>(loader: () => Promise<T>, initial: T) {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Keep the latest loader without making it an effect dependency: an inline
  // arrow at a call site would otherwise tear the stream down every render.
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const ctrl = new AbortController();
    const alive = () => !ctrl.signal.aborted;

    const tick = () =>
      loaderRef
        .current()
        .then((d) => {
          if (alive()) {
            setData(d);
            setError(null);
          }
        })
        .catch((e) => alive() && setError(String(e)))
        .finally(() => alive() && setLoading(false));

    tick();
    if (!LIVE) return () => ctrl.abort();

    const unsubscribe = subscribe(tick);
    const pollId = window.setInterval(tick, POLL_MS);
    return () => {
      ctrl.abort();
      unsubscribe();
      window.clearInterval(pollId);
    };
  }, []);

  return { data, loading, error };
}
