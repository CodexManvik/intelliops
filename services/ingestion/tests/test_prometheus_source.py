import httpx

from services.ingestion.adapters.prometheus_source import PrometheusSource

_OK_BODY = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "http_request_errors_total", "job": "demo-app"},
                "value": [1723700000.0, "7"],
            }
        ],
    },
}


def _source(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return PrometheusSource("http://prom:9090", "some_query", http_client=client)


def test_poll_maps_vector_to_events():
    src = _source(lambda req: httpx.Response(200, json=_OK_BODY))
    events = src.poll()
    assert len(events) == 1
    e = events[0]
    assert e.name == "http_request_errors_total"
    assert e.value == 7.0
    assert e.labels["job"] == "demo-app"


def test_poll_returns_empty_on_connection_error():
    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    src = _source(boom)
    assert src.poll() == []


def test_poll_returns_empty_on_error_status():
    src = _source(lambda req: httpx.Response(200, json={"status": "error"}))
    assert src.poll() == []


def test_poll_returns_empty_on_non_json_body():
    # e.g. a reverse proxy returning an HTML error page with status 200.
    src = _source(lambda req: httpx.Response(200, text="<html>not json</html>"))
    assert src.poll() == []


def test_poll_skips_entry_with_short_value_array():
    bad_body = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "bad_metric", "job": "demo-app"},
                    "value": [1723700000.0],  # missing the value element
                },
                {
                    "metric": {"__name__": "good_metric", "job": "demo-app"},
                    "value": [1723700000.0, "42"],
                },
            ],
        },
    }
    src = _source(lambda req: httpx.Response(200, json=bad_body))
    events = src.poll()
    assert len(events) == 1
    assert events[0].name == "good_metric"
    assert events[0].value == 42.0
