import json
import logging

from common.logging import JsonFormatter, configure_logging


class _S:
    log_level = "INFO"
    log_format = "json"


class _SText(_S):
    log_format = "text"


def _record(msg="hello", level=logging.INFO, exc_info=None):
    return logging.LogRecord("services.foo.app", level, "/x/app.py", 96, msg, None, exc_info)


def test_json_formatter_has_expected_keys():
    rec = _record()
    rec.service = "foo-service"
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "INFO"
    assert out["logger"] == "services.foo.app"
    assert out["service"] == "foo-service"
    assert out["msg"] == "hello"
    assert out["line"] == 96
    assert "ts" in out


def test_json_formatter_includes_exc_info():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = _record(level=logging.ERROR, exc_info=sys.exc_info())
    rec.service = "foo-service"
    out = json.loads(JsonFormatter().format(rec))
    assert "exc_info" in out and "ValueError" in out["exc_info"]


def test_configure_logging_is_idempotent():
    root = logging.getLogger()
    configure_logging("foo-service", _S())
    n = len(root.handlers)
    configure_logging("foo-service", _S())
    assert len(root.handlers) == n, "configure_logging must not stack handlers"


def test_configure_logging_stamps_service_and_emits_json(capsys):
    configure_logging("foo-service", _S())
    logging.getLogger("services.foo.app").info("wired")
    err = capsys.readouterr().err
    # the json line carries the service + message
    line = [ln for ln in err.splitlines() if "wired" in ln][-1]
    payload = json.loads(line)
    assert payload["service"] == "foo-service" and payload["msg"] == "wired"


def test_text_format_is_human_not_json(capsys):
    configure_logging("foo-service", _SText())
    logging.getLogger("services.foo.app").info("plain")
    err = capsys.readouterr().err
    assert "plain" in err
    # text mode is not JSON
    line = [ln for ln in err.splitlines() if "plain" in ln][-1]
    try:
        json.loads(line)
        raise AssertionError("text mode should not emit JSON")
    except json.JSONDecodeError:
        pass
