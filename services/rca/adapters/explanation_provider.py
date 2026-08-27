"""ExplanationProvider implementations for RCA advisory text.

TemplateExplanationProvider is the CI-safe, offline, deterministic default —
no network calls, always available. OpenAICompatibleExplanationProvider talks
to any OpenAI-chat-completions-shaped endpoint (self-hosted or hosted) via a
synchronous httpx.Client, since the RCA consumer is a sync daemon thread, not
async.

Every failure path in OpenAICompatibleExplanationProvider — connection error,
non-200 status, non-JSON body, missing `choices`, empty content — is caught
and falls back to the template output. The LLM call is advisory-only: it must
never raise out of the consumer and must never affect confidence, hypothesis
ordering, or suggested_runbook_id (see consumer.py's model_copy wiring).
"""

from __future__ import annotations

import logging

import httpx

from common.contracts import EnrichmentContext, RootCauseHypothesis, Situation

logger = logging.getLogger("intelliops.rca.explanation")


class TemplateExplanationProvider:
    """Deterministic, offline explanation built from the hypothesis fields."""

    def explain(
        self,
        hypothesis: RootCauseHypothesis,
        context: EnrichmentContext,
        situation: Situation,
    ) -> str:
        return self.explain_with_source(hypothesis, context, situation)[0]

    def explain_with_source(
        self,
        hypothesis: RootCauseHypothesis,
        context: EnrichmentContext,
        situation: Situation,
    ) -> tuple[str, str]:
        runbook = hypothesis.suggested_runbook_id or "no runbook available"
        evidence = "; ".join(hypothesis.evidence) if hypothesis.evidence else "no direct evidence"
        text = (
            f"Likely root cause: {hypothesis.description} "
            f"(confidence {hypothesis.confidence:.2f}). "
            f"Evidence: {evidence}. Suggested runbook: {runbook}."
        )
        return text, "template"


class OpenAICompatibleExplanationProvider:
    """Talks to an OpenAI-chat-completions-shaped endpoint. Falls back to the
    template on ANY failure — this provider must never raise."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 10.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._template = TemplateExplanationProvider()

    def explain(
        self,
        hypothesis: RootCauseHypothesis,
        context: EnrichmentContext,
        situation: Situation,
    ) -> str:
        return self.explain_with_source(hypothesis, context, situation)[0]

    def explain_with_source(
        self,
        hypothesis: RootCauseHypothesis,
        context: EnrichmentContext,
        situation: Situation,
    ) -> tuple[str, str]:
        fallback = self._template.explain(hypothesis, context, situation)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an SRE assistant. Explain the likely root cause of an "
                        "incident concisely, in plain language, for an on-call engineer."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Situation {situation.id} (severity {situation.severity}): "
                        f"hypothesis '{hypothesis.description}' with confidence "
                        f"{hypothesis.confidence:.2f}. Evidence: {hypothesis.evidence}. "
                        f"Suggested runbook: {hypothesis.suggested_runbook_id}. "
                        f"Recent deploys: {context.recent_deploys}."
                    ),
                },
            ],
        }
        try:
            resp = self._client.post(
                f"{self._base}/chat/completions", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.info(
                "llm explanation endpoint unreachable (%s); using template fallback",
                exc.__class__.__name__,
            )
            return fallback, "template"
        if resp.status_code != 200:
            logger.info(
                "llm explanation endpoint returned status %s; using template fallback",
                resp.status_code,
            )
            return fallback, "template"
        try:
            body = resp.json()
        except ValueError as exc:
            logger.info(
                "llm explanation endpoint returned non-JSON body (%s); using template fallback",
                exc.__class__.__name__,
            )
            return fallback, "template"
        try:
            choices = body["choices"]
            content = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.info(
                "llm explanation response missing choices/content (%s); using template fallback",
                exc.__class__.__name__,
            )
            return fallback, "template"
        if not content:
            logger.info("llm explanation response had empty content; using template fallback")
            return fallback, "template"
        return content, "llm"


def make_explanation_provider(
    settings,
) -> TemplateExplanationProvider | OpenAICompatibleExplanationProvider:
    """Selects the LLM-backed provider IFF an endpoint is configured; template
    (no network) otherwise. This is what makes the LLM path opt-in via config
    while explanation itself stays on-by-default."""
    if settings.llm_explanation_endpoint:
        return OpenAICompatibleExplanationProvider(
            base_url=settings.llm_explanation_endpoint,
            model=settings.llm_explanation_model,
            api_key=settings.llm_explanation_api_key,
            timeout_seconds=settings.llm_explanation_timeout_seconds,
        )
    return TemplateExplanationProvider()
