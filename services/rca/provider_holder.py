"""A thread-safe holder for the live ExplanationProvider so the RCA consumer
(a daemon thread) and the /config/llm route (the request thread) can swap it
without a restart. The consumer reads .get() each iteration; the route calls
.set() to install a freshly built provider."""

from __future__ import annotations

import threading


class ProviderHolder:
    def __init__(self, provider) -> None:
        self._provider = provider
        self._lock = threading.Lock()
        self._last_probe: dict | None = None

    def get(self):
        with self._lock:
            return self._provider

    def set(self, provider) -> None:
        with self._lock:
            self._provider = provider

    def set_last_probe(self, probe: dict) -> None:
        with self._lock:
            self._last_probe = probe

    @property
    def last_probe(self) -> dict | None:
        with self._lock:
            return self._last_probe
