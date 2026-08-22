from services.correlation.adapters.baseline_store import InMemoryBaselineStore


def test_inmem_baseline_save_load_roundtrip():
    s = InMemoryBaselineStore()
    rows = [{"metric_name": "cpu_usage", "n": 60.0, "mean": 52.0, "variance": 4.0, "count": 60}]
    s.save(rows)
    got = {r["metric_name"]: r for r in s.load_all()}
    assert got["cpu_usage"]["variance"] == 4.0 and got["cpu_usage"]["count"] == 60


def test_flusher_snapshot_is_best_effort(monkeypatch):
    """A failing baseline_store.save must NOT crash the flusher — logged & skipped."""
    import threading  # noqa: F401

    from services.correlation.consumer import _snapshot_baseline_once  # small extracted helper

    class _Boom:
        def save(self, rows):
            raise RuntimeError("db down")

    class _Engine:
        def snapshot(self):
            return [{"metric_name": "x", "n": 1.0, "mean": 1.0, "variance": 0.0, "count": 1}]

    # must return without raising
    _snapshot_baseline_once(_Engine(), _Boom())
