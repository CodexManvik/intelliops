import { useEffect, useRef, useState } from "react";
import { loadReports, submitData } from "./api";
import { seededClients } from "./mock";

const PERIODS = ["FY26-Q2", "FY26-Q1"];

function randomSubmission() {
  const client = seededClients[Math.floor(Math.random() * seededClients.length)];
  const period = PERIODS[Math.floor(Math.random() * PERIODS.length)];
  const amount = Math.round(Math.random() * 500_000 * 100) / 100;
  return { client, period, amount };
}

export interface TrafficTick {
  ts: number;
  ok: boolean;
  kind: "submission" | "reports";
}

/**
 * Periodically fires real requests at the gateway (POST /api/submissions,
 * GET /api/reports) so the Dashboard/Operations metrics stay live during a
 * demo instead of sitting static. Runs only while `enabled` is true.
 */
export default function useBackgroundTraffic(enabled: boolean, intervalMs = 4000) {
  const [lastTick, setLastTick] = useState<TrafficTick | null>(null);
  const [tickCount, setTickCount] = useState(0);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;

    const fire = async () => {
      const doSubmission = Math.random() < 0.7;
      try {
        if (doSubmission) {
          await submitData(randomSubmission());
        } else {
          await loadReports();
        }
        if (!cancelled) {
          setLastTick({ ts: Date.now(), ok: true, kind: doSubmission ? "submission" : "reports" });
          setTickCount((c) => c + 1);
        }
      } catch {
        if (!cancelled) {
          setLastTick({ ts: Date.now(), ok: false, kind: doSubmission ? "submission" : "reports" });
          setTickCount((c) => c + 1);
        }
      }
    };

    const id = window.setInterval(fire, intervalMs);
    void fire();

    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled, intervalMs]);

  return { lastTick, tickCount };
}
