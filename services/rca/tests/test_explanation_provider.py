from datetime import UTC, datetime

import httpx

from common.contracts import (
    EnrichmentContext,
    RootCauseHypothesis,
    Situation,
    SituationStatus,
    TelemetryEvent,
    TelemetryKind,
)
from services.rca.adapters.explanation_provider import (
    OpenAICompatibleExplanationProvider,
    TemplateExplanationProvider,
    make_explanation_provider,
)

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _situation():
    return Situation(
        id="sit-1",
        status=SituationStatus.DETECTED,
        member_events=[
            TelemetryEvent(
                source="prom",
                kind=TelemetryKind.METRIC,
                name="cpu_usage",
                value=99.0,
                labels={"service": "web"},
                ts=NOW,
                fingerprint="fp",
            )
        ],
        severity="high",
        first_seen=NOW,
        last_seen=NOW,
        signature="sig",
    )


def _hypothesis():
    return RootCauseHypothesis(
        situation_id="sit-1",
        description="resource saturation across the affected service",
        confidence=0.6,
        evidence=["metrics: cpu_usage"],
        suggested_runbook_id="scale-service",
    )


_OK_BODY = {
    "choices": [{"message": {"content": "CPU saturation on web; scale out to relieve pressure."}}]
}


def _provider(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return OpenAICompatibleExplanationProvider(
        base_url="http://llm:8080/v1",
        model="gpt-4o-mini",
        api_key="key",
        timeout_seconds=5.0,
        http_client=client,
        **kwargs,
    )


def test_template_is_offline_deterministic():
    provider = TemplateExplanationProvider()
    ctx = EnrichmentContext()
    sit = _situation()
    hyp = _hypothesis()
    first = provider.explain(hyp, ctx, sit)
    second = provider.explain(hyp, ctx, sit)
    assert first == second
    assert isinstance(first, str)
    assert len(first) > 0


def test_template_mentions_hypothesis_and_runbook():
    provider = TemplateExplanationProvider()
    hyp = _hypothesis()
    text = provider.explain(hyp, EnrichmentContext(), _situation())
    assert "resource saturation" in text.lower()
    assert "scale-service" in text


def test_posts_openai_shape():
    import json

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_OK_BODY)

    provider = _provider(handler)
    text = provider.explain(_hypothesis(), EnrichmentContext(), _situation())

    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "gpt-4o-mini"
    messages = captured["payload"]["messages"]
    assert any(m.get("role") == "system" for m in messages)
    assert text == "CPU saturation on web; scale out to relieve pressure."


def test_connect_error_falls_back_to_template():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = _provider(boom)
    template = TemplateExplanationProvider()
    hyp = _hypothesis()
    ctx = EnrichmentContext()
    sit = _situation()

    result = provider.explain(hyp, ctx, sit)
    assert result == template.explain(hyp, ctx, sit)


def test_non_200_falls_back_to_template():
    provider = _provider(lambda req: httpx.Response(500, text="server error"))
    template = TemplateExplanationProvider()
    hyp = _hypothesis()
    ctx = EnrichmentContext()
    sit = _situation()

    result = provider.explain(hyp, ctx, sit)
    assert result == template.explain(hyp, ctx, sit)


def test_non_json_body_falls_back_to_template():
    provider = _provider(lambda req: httpx.Response(200, text="<html>not json</html>"))
    template = TemplateExplanationProvider()
    hyp = _hypothesis()
    ctx = EnrichmentContext()
    sit = _situation()

    result = provider.explain(hyp, ctx, sit)
    assert result == template.explain(hyp, ctx, sit)


def test_missing_choices_falls_back_to_template():
    provider = _provider(lambda req: httpx.Response(200, json={"id": "abc"}))
    template = TemplateExplanationProvider()
    hyp = _hypothesis()
    ctx = EnrichmentContext()
    sit = _situation()

    result = provider.explain(hyp, ctx, sit)
    assert result == template.explain(hyp, ctx, sit)


def test_empty_content_falls_back_to_template():
    empty_body = {"choices": [{"message": {"content": ""}}]}
    provider = _provider(lambda req: httpx.Response(200, json=empty_body))
    template = TemplateExplanationProvider()
    hyp = _hypothesis()
    ctx = EnrichmentContext()
    sit = _situation()

    result = provider.explain(hyp, ctx, sit)
    assert result == template.explain(hyp, ctx, sit)


def test_none_of_the_error_paths_raise():
    error_handlers = [
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
        lambda req: httpx.Response(503, text="unavailable"),
        lambda req: httpx.Response(200, text="not json"),
        lambda req: httpx.Response(200, json={}),
        lambda req: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
    ]
    for handler in error_handlers:
        provider = _provider(handler)
        # Must not raise.
        result = provider.explain(_hypothesis(), EnrichmentContext(), _situation())
        assert isinstance(result, str)
        assert len(result) > 0


def test_factory_selects_template_when_endpoint_empty():
    class Settings:
        llm_explanation_endpoint = ""
        llm_explanation_model = "gpt-4o-mini"
        llm_explanation_timeout_seconds = 10.0
        llm_explanation_api_key = ""

    provider = make_explanation_provider(Settings())
    assert isinstance(provider, TemplateExplanationProvider)


def test_factory_selects_openai_compatible_when_endpoint_set():
    class Settings:
        llm_explanation_endpoint = "http://llm:8080/v1"
        llm_explanation_model = "gpt-4o-mini"
        llm_explanation_timeout_seconds = 10.0
        llm_explanation_api_key = "key"

    provider = make_explanation_provider(Settings())
    assert isinstance(provider, OpenAICompatibleExplanationProvider)
