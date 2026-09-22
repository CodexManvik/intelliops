"""Keep a background worker alive across unexpected exceptions.

Every service runs its bus consumer on a daemon thread. Before this, any
exception a handler didn't catch - a governance restart surfacing as a
ConnectError from the HTTP gate, a Postgres blip in an audit write - ended the
thread for good. Nothing noticed: /health is static, so Kubernetes never
restarted the pod, and the service sat "ready" while processing nothing.

`start_supervised` runs the target again after a crash, with capped exponential
backoff, until the stop event is set. A target that RETURNS is done (the stream
ended, or it saw the stop event) and is not restarted.

Restarting is safe under both delivery modes: at-least-once re-serves the
un-acked entry on the next consume() (and the DLQ parks it after
bus_max_delivery_attempts, so a poison event can't loop forever); at-most-once
has already acked it, which is exactly the loss that mode accepts.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("intelliops.supervise")


def supervise(
    name: str,
    target: Callable[..., object],
    stop_event: threading.Event,
    args: tuple = (),
    kwargs: dict | None = None,
    backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 30.0,
) -> None:
    kwargs = kwargs or {}
    delay = backoff_seconds
    while not stop_event.is_set():
        try:
            target(*args, **kwargs)
            return
        except Exception:
            logger.exception("%s crashed; restarting in %.1fs", name, delay)
            if stop_event.wait(delay):
                return
            delay = min(delay * 2, max_backoff_seconds)


def start_supervised(
    name: str,
    target: Callable[..., object],
    stop_event: threading.Event,
    args: tuple = (),
    kwargs: dict | None = None,
) -> threading.Thread:
    """Start `target(*args, **kwargs)` on a supervised daemon thread."""
    thread = threading.Thread(
        target=supervise,
        name=name,
        args=(name, target, stop_event, args, kwargs),
        daemon=True,
    )
    thread.start()
    return thread
