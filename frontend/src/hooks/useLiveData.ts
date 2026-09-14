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

/* ---------------------------------------------------------------------------
   Connection health, shared.

   `loading` and `error` were returned by this hook and consumed by nobody, so a
   backend that had fallen over looked exactly like a healthy, quiet fleet:
   zeros everywhere and a "streaming" badge. This tracks how many loaders are
   currently failing so the shell can say so out loud.
--------------------------------------------------------------------------- */

// Keyed by loader, holding the time of the last FAILURE. A success clears the
// entry outright. Health is then derived from recency rather than from a sticky
// flag: if a loader stops reporting entirely (its component unmounted mid-flight,
// say) its stale failure ages out instead of pinning the banner on screen
// forever - which is exactly what a set-of-failing-keys did.
const _lastFailure = new Map<string, number>();
const _healthListeners = new Set<(n: number) => void>();
const STALE_FAILURE_MS = 20_000;
let _seq = 0;

function failingCount(): number {
  const cutoff = Date.now() - STALE_FAILURE_MS;
  let n = 0;
  for (const [key, at] of _lastFailure) {
    if (at < cutoff) _lastFailure.delete(key);
    else n++;
  }
  return n;
}

function emitHealth() {
  const n = failingCount();
  _healthListeners.forEach((l) => l(n));
}

function reportHealth(key: string, ok: boolean) {
  if (ok) _lastFailure.delete(key);
  else _lastFailure.set(key, Date.now());
  emitHealth();
}

/** Subscribe to the number of currently-failing loaders. */
export function onConnectionHealth(fn: (failing: number) => void): () => void {
  _healthListeners.add(fn);
  fn(failingCount());
  // Re-evaluate on a timer too, so a failure that simply stops being retried
  // ages out of the banner on its own.
  const id = window.setInterval(emitHealth, 5000);
  return () => {
    _healthListeners.delete(fn);
    window.clearInterval(id);
  };
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

    const key = `l${++_seq}`;
    const tick = () =>
      loaderRef
        .current()
        .then((d) => {
          if (alive()) {
            setData(d);
            setError(null);
            reportHealth(key, true);
          }
        })
        .catch((e) => {
          if (alive()) {
            setError(String(e));
            reportHealth(key, false);
          }
        })
        .finally(() => alive() && setLoading(false));

    tick();
    if (!LIVE) return () => ctrl.abort();

    const unsubscribe = subscribe(tick);
    const pollId = window.setInterval(tick, POLL_MS);
    return () => {
      ctrl.abort();
      reportHealth(key, true); // unmounting is not a failure
      unsubscribe();
      window.clearInterval(pollId);
    };
  }, []);

  return { data, loading, error };
}
