"""RunbookAuthor implementations: draft a typed Playbook for a gap.

NullRunbookAuthor is the CI-safe default — no network, always None.

OpenAICompatibleRunbookAuthor talks to any OpenAI-chat-completions-shaped
endpoint via a synchronous httpx.Client and parses the model's content into a
typed Playbook in ONE shot (no tools). It NEVER raises: any failure —
transport, non-200, non-JSON, missing content, content that isn't JSON, or a
Playbook that fails validation (e.g. an out-of-set action) — returns None (no
draft). The closed RemediationStep Literal is what actually rejects unsafe
actions; the prompt only asks nicely.

RunbookAuthorAgent is the tool-calling successor: instead of asking the model
to draft blind, it hands the model read-tools (TOOL_SCHEMAS from
author_tools.py) so it can gather situation/system/history context before
drafting, and ends the loop only when the model calls `submit_runbook`. It
reuses the same HTTP-with-retry machinery (429 backoff, token cap) as
OpenAICompatibleRunbookAuthor via `_post_chat_completion`. It NEVER raises.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from common.contracts import Playbook, Situation

if TYPE_CHECKING:
    from services.governance.adapters.author_tools import AuthorToolbox

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

# The AI does NOT author the id — the prompt never asks for one and
# propose_playbook assigns a server-side `ai-<sig>-<uuid>` right after (the AI
# setting an id is exactly what we must not trust). But Playbook.id is
# required, so a draft that (correctly) omits it would always fail
# validation. Inject a placeholder purely to validate the parts the AI DOES
# author (name/match_rule/steps/hitl/rollback); the server overwrites it, so
# the placeholder never escapes. The closed RemediationStep Literal — the
# load-bearing safety gate — still runs.
_PLACEHOLDER_ID = "ai-draft-pending"


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


def _inject_placeholder_id(draft: Any) -> Any:
    """Carry the #47 fix: a real draft omits `id`; Playbook requires it.

    Inject a placeholder ONLY when absent so validation exercises everything
    the model DOES author, without ever trusting a model-supplied id (the
    server always assigns the real one downstream).
    """
    if isinstance(draft, dict) and "id" not in draft:
        return {**draft, "id": _PLACEHOLDER_ID}
    return draft


def _post_chat_completion(
    client,
    base_url: str,
    api_key: str,
    payload: dict,
) -> tuple[httpx.Response | Any | None, str, float | None]:
    """POST one chat/completions call and classify the TRANSPORT-level outcome.

    Shared by OpenAICompatibleRunbookAuthor (single-shot) and
    RunbookAuthorAgent (tool-calling loop) so the 429/backoff/transport/
    non-200 handling lives in exactly one place. Deliberately stops at the
    HTTP layer — it does NOT parse the response body/message shape, because
    the two callers want different things from a 200 (single-shot wants
    `message["content"]` as a JSON string; the agent wants the whole message,
    possibly with `tool_calls`) and each has its own (already-tested) policy
    for what counts as a retryable "invalid" body vs. giving up.

    Returns (resp, outcome, retry_after):
    - resp: the raw response object on "ok" (call `.json()` yourself), else None.
    - outcome: "ok" | "rate_limited" (429) | "terminal" (transport error or
      other non-200 — neither heals within this helper).
    - retry_after: seconds the server asked us to wait on a 429, else None.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.info("runbook author endpoint unreachable (%s); no draft", exc.__class__.__name__)
        return None, "terminal", None  # transport failure — do not retry
    if resp.status_code == 429:
        # Rate limited: recoverable, but only after a wait. Honor the delay
        # the server advises (header first, then the message body).
        return None, "rate_limited", _parse_retry_after(resp)
    if resp.status_code != 200:
        logger.info("runbook author endpoint status %s; no draft", resp.status_code)
        return None, "terminal", None
    return resp, "ok", None


def _rate_limit_delay(retry_after: float | None) -> float:
    """Clamp a server-advised (or default) 429 backoff to the sane ceiling."""
    return min(retry_after or _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS, _MAX_RATE_LIMIT_BACKOFF_SECONDS)


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
                delay = _rate_limit_delay(retry_after)
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
        resp, outcome, retry_after = _post_chat_completion(
            self._client, self._base, self._api_key, payload
        )
        if outcome == "rate_limited":
            return None, "rate_limited", retry_after
        if outcome == "terminal":
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
            draft = _inject_placeholder_id(parsed["playbook"])
            playbook = Playbook.model_validate(draft)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            logger.info(
                "runbook author draft did not validate (%s); no draft", exc.__class__.__name__
            )
            return None, "invalid", None
        rationale = parsed.get("rationale") if isinstance(parsed, dict) else None
        return (playbook, rationale), "ok", None


_AGENT_SYSTEM_PROMPT = (
    "You are an SRE assistant that drafts a Kubernetes remediation runbook by "
    "investigating an incident, then submitting a typed draft. You have "
    "read-only tools to gather context: use them before drafting. "
    "STRICT RULES: "
    "You MUST finish by calling the `submit_runbook` tool — that is the ONLY "
    "way to end this task; plain text alone finishes nothing. "
    f"Each step action MUST be one of: {_ALLOWED}. Any other action is rejected. "
    "Numeric fields MUST be concrete integers, never placeholders or words: "
    'for a scale step give "replicas" as a real integer delta (e.g. 2 or -1), '
    'and for a rollback_to_revision step give "revision" as a real integer '
    "(e.g. 3). Do NOT emit template tokens like {{...}} or words like "
    '"previous" for any number. Do not include an "id" field on the playbook '
    "— it is assigned server-side. "
    "Note: `get_human_decisions` returns the most RECENT proposal decisions "
    "in general — it is not necessarily about this incident's signature; "
    "weigh it as general context, not as a direct precedent for this case."
)

_NUDGE_MESSAGE = "Call submit_runbook to finish."


def _build_initial_messages(situation: Situation, hint: str | None) -> list[dict]:
    return [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Incident {situation.id} (severity {situation.severity}, signature "
                f"{situation.signature}) has no matching runbook. Investigate using "
                f"the available tools, then draft one. Hint: {hint or 'none'}."
            ),
        },
    ]


def _parse_submit_arguments(raw_arguments: Any) -> tuple[dict, str, list[str]]:
    """Parse submit_runbook's JSON `arguments` string into (playbook, rationale, cited_facts).

    Raises (json.JSONDecodeError, KeyError, TypeError) on malformance — the
    caller treats that identically to a ValidationError (a corrective round).
    """
    parsed = json.loads(raw_arguments)
    playbook = parsed["playbook"]
    rationale = parsed.get("rationale", "")
    cited_facts = parsed.get("cited_facts", [])
    if not isinstance(playbook, dict):
        raise TypeError("playbook must be an object")
    if not isinstance(cited_facts, list):
        raise TypeError("cited_facts must be an array")
    return playbook, rationale, cited_facts


class RunbookAuthorAgent:
    """Bounded tool-calling agent: gathers context via read-tools, then drafts.

    Each `.draft()` call builds a fresh AuthorToolbox (via `toolbox_factory`,
    bound to the situation) and runs a chat/tool-call loop for up to
    `max_rounds` rounds. The loop ends only when the model calls
    `submit_runbook` with a payload that validates as a Playbook — anything
    else (budget exhaustion, transport failure, malformed response, an
    exception anywhere) yields None. NEVER raises.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        toolbox_factory: Callable[[Situation], AuthorToolbox] | None = None,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        max_rounds: int = 6,
        http_client=None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._toolbox_factory = toolbox_factory
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        # Bounds the 429-retry budget for a single round (mirrors the
        # single-shot author's rate-limit patience) — NOT the number of
        # drafting rounds, which is max_rounds.
        self._max_attempts = max(1, max_attempts)
        self._max_rounds = max(1, max_rounds)

    def draft(
        self, situation: Situation, hint: str | None = None
    ) -> tuple[Playbook, str, list[str]] | None:
        try:
            return self._run(situation, hint)
        except Exception:
            logger.exception("runbook author agent: unhandled exception; no draft")
            return None

    def _run(
        self, situation: Situation, hint: str | None
    ) -> tuple[Playbook, str, list[str]] | None:
        if self._toolbox_factory is None:
            logger.info("runbook author agent: no toolbox_factory configured; no draft")
            return None
        toolbox = self._toolbox_factory(situation)
        messages = _build_initial_messages(situation, hint)
        nudged = False

        for _round in range(self._max_rounds):
            message, outcome = self._call_model(messages)
            if outcome != "ok":
                return None  # rate-limit budget exhausted or a terminal HTTP failure

            tool_calls = message.get("tool_calls")
            if tool_calls:
                nudged = False  # the model is engaging with tools again
                # Echo the assistant turn back before any tool results, exactly
                # as the OpenAI tool-calling protocol requires.
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": tool_calls,
                    }
                )
                for call in tool_calls:
                    result = self._handle_tool_call(call, toolbox)
                    if result.get("_submitted"):
                        return result["_submitted"]
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(result["content"]),
                        }
                    )
                continue

            # No tool call: plain content only. Nudge once; if the model still
            # won't call submit_runbook, let the round budget end the loop.
            messages.append({"role": "assistant", "content": message.get("content")})
            if nudged:
                continue  # already nudged once — burn remaining rounds, then give up
            messages.append({"role": "user", "content": _NUDGE_MESSAGE})
            nudged = True

        logger.info(
            "runbook author agent: exhausted %d rounds without a valid submit", self._max_rounds
        )
        return None

    def _call_model(self, messages: list[dict]) -> tuple[dict | None, str]:
        """POST one round, honoring the 429 backoff within this round's attempt budget.

        Returns (message, outcome) where outcome is "ok" (message is the raw
        assistant message dict) or "terminal" (budget exhausted / transport /
        non-200 — draft() should give up).
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "tools": _tool_schemas(),
            "tool_choice": "auto",
            "max_tokens": _MAX_COMPLETION_TOKENS,
        }
        for attempt in range(self._max_attempts):
            resp, outcome, retry_after = _post_chat_completion(
                self._client, self._base, self._api_key, payload
            )
            if outcome == "ok":
                try:
                    body = resp.json()
                    message = body["choices"][0]["message"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    logger.info(
                        "runbook author agent response missing/invalid message (%s); no draft",
                        exc.__class__.__name__,
                    )
                    return None, "terminal"
                if not isinstance(message, dict):
                    return None, "terminal"
                return message, "ok"
            last = attempt + 1 >= self._max_attempts
            if outcome == "rate_limited" and not last:
                delay = _rate_limit_delay(retry_after)
                logger.info(
                    "runbook author agent rate-limited (429); backing off %.1fs then retrying",
                    delay,
                )
                time.sleep(delay)
                continue
            return None, "terminal"
        return None, "terminal"

    def _handle_tool_call(self, call: dict, toolbox: AuthorToolbox) -> dict:
        """Execute one tool call. Returns {"content": <tool-result-dict>} for a
        normal tool result message, or {"_submitted": (playbook, rationale,
        cited_facts)} when submit_runbook validated successfully (the caller
        returns immediately in that case, without appending a tool message).
        """
        function = call.get("function", {}) or {}
        name = function.get("name", "")
        raw_arguments = function.get("arguments") or "{}"

        if name == "submit_runbook":
            try:
                playbook_dict, rationale, cited_facts = _parse_submit_arguments(raw_arguments)
                validated = Playbook.model_validate(_inject_placeholder_id(playbook_dict))
            except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
                logger.info(
                    "runbook author agent: submit_runbook draft invalid (%s); requesting resubmit",
                    exc.__class__.__name__,
                )
                return {"content": {"error": f"invalid: {exc}; fix and resubmit"}}
            return {"_submitted": (validated, rationale, cited_facts)}

        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(arguments, dict):
                arguments = {}
        except (json.JSONDecodeError, TypeError):
            arguments = {}
        # AuthorToolbox.dispatch never raises (it degrades to {"error": ...}
        # internally), but a fake toolbox in tests might — guard anyway so a
        # single bad tool call can never crash the whole drafting loop.
        try:
            result = toolbox.dispatch(name, arguments)
        except Exception as exc:
            logger.exception("runbook author agent: toolbox.dispatch raised during %r", name)
            result = {"error": type(exc).__name__}
        return {"content": result}


def _tool_schemas() -> list[dict]:
    # Imported lazily (function-local) to avoid a module-level import cycle
    # risk and to keep runbook_author.py importable standalone if author_tools
    # ever grows a heavier dependency — it currently doesn't (stdlib +
    # common.contracts only), but this keeps the boundary explicit.
    from services.governance.adapters.author_tools import TOOL_SCHEMAS

    return TOOL_SCHEMAS
