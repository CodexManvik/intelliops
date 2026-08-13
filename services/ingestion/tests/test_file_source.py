from common.contracts import TelemetryEvent
from common.interfaces import TelemetrySource
from services.ingestion.adapters.file_source import FileTelemetrySource

SAMPLE = (
    '{"source":"prom","kind":"metric","name":"cpu","value":0.9,'
    '"labels":{"pod":"web-1"},"ts":"2026-08-13T00:00:00+00:00"}\n'
    '{"source":"prom","kind":"metric","name":"cpu","value":0.95,'
    '"labels":{"pod":"web-2"},"ts":"2026-08-13T00:00:01+00:00"}\n'
)


def test_file_source_satisfies_protocol(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE)
    src = FileTelemetrySource(str(f))
    assert isinstance(src, TelemetrySource)


def test_poll_reads_and_normalizes_all_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE)
    events = FileTelemetrySource(str(f)).poll()
    assert len(events) == 2
    assert all(isinstance(e, TelemetryEvent) for e in events)
    assert events[0].name == "cpu"
    assert events[1].labels == {"pod": "web-2"}


def test_poll_skips_blank_lines(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE + "\n   \n")
    assert len(FileTelemetrySource(str(f)).poll()) == 2


def test_subscribe_yields_each_event(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text(SAMPLE)
    out = list(FileTelemetrySource(str(f)).subscribe())
    assert len(out) == 2
