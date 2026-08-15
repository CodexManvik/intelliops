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
