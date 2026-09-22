import threading

from common.supervise import start_supervised, supervise


def test_restarts_after_a_crash_then_finishes():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("boom")

    supervise("t", flaky, threading.Event(), backoff_seconds=0.0)
    assert len(calls) == 3


def test_a_normal_return_is_not_restarted():
    calls = []
    supervise("t", lambda: calls.append(1), threading.Event(), backoff_seconds=0.0)
    assert calls == [1]


def test_stop_event_ends_the_retry_loop():
    stop = threading.Event()
    calls = []

    def always_fails():
        calls.append(1)
        stop.set()
        raise RuntimeError("boom")

    supervise("t", always_fails, stop, backoff_seconds=0.0)
    assert calls == [1]


def test_passes_args_and_kwargs_on_a_real_thread():
    got = []
    t = start_supervised(
        "t", lambda a, b=None: got.append((a, b)), threading.Event(), (1,), {"b": 2}
    )
    t.join(timeout=5)
    assert got == [(1, 2)]
