"""RunbookAuthor implementations: draft a typed Playbook for a gap.

NullRunbookAuthor is the CI-safe default — no network, always None.
OpenAICompatibleRunbookAuthor talks to any OpenAI-chat-completions-shaped
endpoint via a synchronous httpx.Client and parses the model's content into a
typed Playbook. It NEVER raises: any failure — transport, non-200, non-JSON,
missing content, content that isn't JSON, or a Playbook that fails validation
(e.g. an out-of-set action) — returns None (no draft). The closed
RemediationStep Literal is what actually rejects unsafe actions; the prompt
only asks nicely."""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import ValidationError

from common.contracts import Playbook, Situation

logger = logging.getLogger("intelliops.governance.runbook_author")

_ALLOWED = "restart, scale, rollback_deploy, wait, patch_resource_limits, rollback_to_revision, patch_probe"


class NullRunbookAuthor:
    def draft(self, situation: Situation, hint: str | None = None):
        return None


class OpenAICompatibleRunbookAuthor:
    def __init__(self, base_url, model, api_key="", timeout_seconds=10.0, http_client=None):
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def draft(self, situation: Situation, hint: str | None = None):
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an SRE assistant that writes Kubernetes remediation runbooks. "
                        "Respond with STRICT JSON only, shaped as "
                        '{"playbook": {"name": str, "match_rule": str, "steps": [{"action": str, ...}], '
                        '"hitl_mode": "hitl", "reversible": bool, "rollback_steps": [...]}, "rationale": str}. '
                        f"Each step action MUST be one of: {_ALLOWED}. Any other action is rejected."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Incident {situation.id} (severity {situation.severity}, signature "
                        f"{situation.signature}) has no matching runbook. Draft one. Hint: {hint or 'none'}."
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
                "runbook author endpoint unreachable (%s); no draft", exc.__class__.__name__
            )
            return None
        if resp.status_code != 200:
            logger.info("runbook author endpoint status %s; no draft", resp.status_code)
            return None
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.info(
                "runbook author response missing/invalid content (%s); no draft",
                exc.__class__.__name__,
            )
            return None
        if not content:
            return None
        try:
            parsed = json.loads(content)
            playbook = Playbook.model_validate(parsed["playbook"])
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            logger.info(
                "runbook author draft did not validate (%s); no draft", exc.__class__.__name__
            )
            return None
        rationale = parsed.get("rationale") if isinstance(parsed, dict) else None
        return playbook, rationale
