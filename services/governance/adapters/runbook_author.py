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
import re
import time

import httpx
from pydantic import ValidationError

from common.contracts import Playbook, Situation

logger = logging.getLogger("intelliops.governance.runbook_author")

_ALLOWED = "restart, scale, rollback_deploy, wait, patch_resource_limits, rollback_to_revision, patch_probe"

# Cap the completion size. A runbook draft is small; a tight ceiling keeps each
# call cheap against a token-per-minute quota (e.g. Groq free tier = 8000 TPM),
# which is what actually throttles repeated drafting — not model quality.
_MAX_COMPLETION_TOKENS = 1200
# When a 429 doesn't tell us how long to wait, back off this long before retry.
_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 5.0
# Never sleep longer than this on a single 429 (don't hang the request forever).
_MAX_RATE_LIMIT_BACKOFF_SECONDS = 15.0

# "...try again in 6.51s..." / "...in 1m2.5s..." — the delay an OpenAI-compatible
# 429 body advises. Captures an optional minutes group and a seconds group.
_RETRY_AFTER_BODY_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.IGNORECASE)


def _parse_retry_after(resp) -> float | None:
    """Seconds to wait before retrying a 429, or None if unknown.

    Prefer the standard Retry-After header; fall back to the delay embedded in
    an OpenAI-compatible error body ("Please try again in 6.51s"). Never raises
    — a malformed response just yields None (caller uses its default backoff).
    """
    try:
        header = resp.headers.get("retry-after")
        if header:
            return float(header)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        body = resp.json()
        message = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
        match = _RETRY_AFTER_BODY_RE.search(message)
        if match:
            minutes = float(match.group(1)) if match.group(1) else 0.0
            return minutes * 60.0 + float(match.group(2))
    except (ValueError, AttributeError, TypeError):
        pass
    return None


class NullRunbookAuthor:
    def draft(self, situation: Situation, hint: str | None = None):
        return None


class OpenAICompatibleRunbookAuthor:
    def __init__(
        self,
        base_url,
        model,
        api_key="",
        timeout_seconds=10.0,
        http_client=None,
        max_attempts=3,
    ):
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        # Two recoverable failures justify a retry: a bad roll (a draft that
        # misses the closed schema — often a placeholder where an int belongs)
        # and a 429 (a token-per-minute quota; retried AFTER the advised delay).
        # So one operator "Draft" click reliably yields a runbook without
        # hammering a rate-limited endpoint.
        self._max_attempts = max(1, max_attempts)

    def draft(self, situation: Situation, hint: str | None = None):
        # Retry only RECOVERABLE failures:
        #   - a draft that didn't validate (a bad roll — try once more), and
        #   - HTTP 429 rate limiting (wait the server-advised delay, then retry;
        #     a token-per-minute quota is the usual reason repeated drafting
        #     fails, so blind immediate retries only make it worse).
        # A transport error or any other non-200 won't heal in the loop → stop.
        for attempt in range(self._max_attempts):
            result, outcome, retry_after = self._attempt_draft(situation, hint)
            if result is not None:
                return result
            last = attempt + 1 >= self._max_attempts
            if outcome == "rate_limited" and not last:
                delay = min(
                    retry_after or _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
                    _MAX_RATE_LIMIT_BACKOFF_SECONDS,
                )
                logger.info(
                    "runbook author rate-limited (429); backing off %.1fs then retrying", delay
                )
                time.sleep(delay)
                continue
            if outcome == "invalid" and not last:
                logger.info(
                    "runbook author draft attempt %d/%d did not validate; retrying",
                    attempt + 1,
                    self._max_attempts,
                )
                continue
            return None  # terminal outcome (transport / other non-200), or budget spent
        return None

    def _attempt_draft(self, situation: Situation, hint: str | None):
        """One draft round. Returns (result, outcome, retry_after):
        - result: (playbook, rationale) on success, else None
        - outcome: "ok" | "invalid" (bad draft) | "rate_limited" (429) |
          "terminal" (transport error or other non-200 — do not retry)
        - retry_after: seconds the server asked us to wait on a 429, else None
        """
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
                        f"Each step action MUST be one of: {_ALLOWED}. Any other action is rejected. "
                        "Numeric fields MUST be concrete integers, never placeholders or words: "
                        'for a scale step give "replicas" as a real integer delta (e.g. 2 or -1), and '
                        'for a rollback_to_revision step give "revision" as a real integer (e.g. 3). '
                        'Do NOT emit template tokens like {{...}} or words like "previous" for any number. '
                        'Do not include an "id" field — it is assigned server-side.'
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
            "max_tokens": _MAX_COMPLETION_TOKENS,
        }
        try:
            resp = self._client.post(
                f"{self._base}/chat/completions", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.info(
                "runbook author endpoint unreachable (%s); no draft", exc.__class__.__name__
            )
            return None, "terminal", None  # transport failure — do not retry
        if resp.status_code == 429:
            # Rate limited: recoverable, but only after a wait. Honor the delay
            # the server advises (header first, then the message body).
            return None, "rate_limited", _parse_retry_after(resp)
        if resp.status_code != 200:
            logger.info("runbook author endpoint status %s; no draft", resp.status_code)
            return None, "terminal", None
        try:
            body = resp.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.info(
                "runbook author response missing/invalid content (%s); no draft",
                exc.__class__.__name__,
            )
            return None, "invalid", None
        if not content:
            return None, "invalid", None
        try:
            parsed = json.loads(content)
            draft = parsed["playbook"]
            # The AI does NOT author the id — the prompt never asks for one and
            # propose_playbook assigns a server-side `ai-<sig>-<uuid>` right after
            # (the AI setting an id is exactly what we must not trust). But
            # Playbook.id is required, so a draft that (correctly) omits it would
            # always fail validation. Inject a placeholder purely to validate the
            # parts the AI DOES author (name/match_rule/steps/hitl/rollback); the
            # server overwrites it, so the placeholder never escapes. The closed
            # RemediationStep Literal — the load-bearing safety gate — still runs.
            if isinstance(draft, dict) and "id" not in draft:
                draft = {**draft, "id": "ai-draft-pending"}
            playbook = Playbook.model_validate(draft)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            logger.info(
                "runbook author draft did not validate (%s); no draft", exc.__class__.__name__
            )
            return None, "invalid", None
        rationale = parsed.get("rationale") if isinstance(parsed, dict) else None
        return (playbook, rationale), "ok", None
