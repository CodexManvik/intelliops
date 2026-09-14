"""Measure the bus's delivery guarantee, with numbers.

Publishes N events to a scratch topic, consumes them with a handler that raises
partway through (a crash mid-processing), then reconnects with the same consumer
group and drains the rest. It reports how many events the handler actually
completed, and therefore how many were lost.

Under **at-most-once** (today's default: `RedisBus.consume` XACKs an entry
immediately BEFORE yielding it) the entry that was in flight when the handler
raised has already been acked, so it is never redelivered and `lost` is
non-zero.

Under **at-least-once** that entry is still pending, is redelivered on
reconnect, and `lost` is 0.

A trailing sentinel event bounds the run: `RedisBus.consume` blocks and retries
forever by design, so the drain stops when it sees the sentinel rather than
waiting on an empty stream.

Usage (against a running Redis, e.g. the compose stack):

    INTELLIOPS_REDIS_URL=redis://localhost:6379 python scripts/delivery_probe.py
    ... --count 200 --crash-at 100

Run it before and after flipping the ack point; the two `lost` numbers are the
evidence quoted in docs/OPERATIONS.md "Delivery guarantees".
"""

from __future__ import annotations

import argparse
import sys
import uuid

from common.bus import make_bus
from common.config import Settings

_SENTINEL = "__end__"


class _Crash(RuntimeError):
    """Simulated handler failure - a DB error, a validation error, or a kill."""


def _drain(bus, topic: str, group: str, seen: set[str], crash_at: int | None) -> None:
    """Consume until the sentinel, or until `crash_at` completions."""
    for msg in bus.consume(topic, group):
        if msg.get("id") == _SENTINEL:
            return
        if crash_at is not None and len(seen) == crash_at:
            # Raise BEFORE recording it: the handler died mid-processing, so this
            # event was received but never actually handled.
            raise _Crash(f"handler crashed while processing event #{msg.get('n')}")
        seen.add(msg["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure bus delivery loss.")
    ap.add_argument("--count", type=int, default=50, help="events to publish")
    ap.add_argument("--crash-at", type=int, default=25, help="crash after N completions")
    args = ap.parse_args()

    if not 0 < args.crash_at < args.count:
        print(f"--crash-at must be between 1 and {args.count - 1}", file=sys.stderr)
        return 2

    settings = Settings()
    run = uuid.uuid4().hex[:8]
    topic = f"probe.delivery.{run}"
    group = "probe"

    bus = make_bus(settings, consumer_name=f"probe-{run}")
    for n in range(args.count):
        bus.publish(topic, {"id": f"{run}-{n}", "n": n})
    bus.publish(topic, {"id": _SENTINEL, "n": -1})

    seen: set[str] = set()

    try:
        _drain(bus, topic, group, seen, crash_at=args.crash_at)
    except _Crash as exc:
        crashed_on = str(exc)
    else:
        print("handler never crashed - stream shorter than --crash-at", file=sys.stderr)
        return 2

    completed_before = len(seen)

    # A fresh connection on the same group AND THE SAME CONSUMER NAME: that is
    # what a restarted service looks like. The name matters - the pending-entry
    # self-drain re-serves entries recorded against this consumer, so a restart
    # under a new name would strand them (see ADR-033 / bus_consumer_name).
    bus2 = make_bus(settings, consumer_name=f"probe-{run}")
    _drain(bus2, topic, group, seen, crash_at=None)

    completed = len(seen)
    lost = args.count - completed

    print(f"delivery mode      {getattr(bus, '_delivery', 'at_most_once')}")
    print(f"topic              {topic}")
    print(f"{crashed_on}")
    print()
    print(f"published          {args.count}")
    print(f"completed (pre)    {completed_before}")
    print(f"completed (total)  {completed}")
    print(f"LOST               {lost}")
    print()
    if lost:
        print(f"AT-MOST-ONCE: {lost} event(s) acked but never handled - not redelivered.")
    else:
        print("AT-LEAST-ONCE: every event was handled; the in-flight one came back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
