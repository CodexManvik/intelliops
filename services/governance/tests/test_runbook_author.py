import json
from datetime import UTC, datetime

import httpx
import pytest

from common.contracts import Situation, SituationStatus
from services.governance.adapters.runbook_author import (
    NullRunbookAuthor,
    OpenAICompatibleRunbookAuthor,
    _parse_retry_after,
)


def _situation():
    now = datetime.now(UTC)
    return Situation(
        id="sit-1",
        status=SituationStatus.DIAGNOSED,
        severity="high",
        first_seen=now,
        last_seen=now,
        signature="sig-1",
    )


class _FakeResp:
    def __init__(self, status_code=200, body=None, raise_json=False, headers=None):
        self.status_code = status_code
        self._body = body
        self._raise_json = raise_json
        self.headers = headers or {}

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    def __init__(self, resp=None, raise_http=False, resp_sequence=None):
        # resp: same response every call (back-compat).
        # resp_sequence: a list of responses returned one-per-call, in order —
        # used to exercise the retry loop (e.g. [bad, bad, good]).
        self._resp = resp
        self._raise_http = raise_http
        self._sequence = list(resp_sequence) if resp_sequence is not None else None
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        if self._raise_http:
            raise httpx.ConnectError("unreachable")
        if self._sequence is not None:
            # clamp to the last element once exhausted
            idx = min(self.calls - 1, len(self._sequence) - 1)
            return self._sequence[idx]
        return self._resp


def _content(playbook_json: dict, rationale="because") -> dict:
    # an OpenAI-chat-shaped body whose message content is the JSON draft
    inner = {"playbook": playbook_json, "rationale": rationale}
    return {"choices": [{"message": {"content": json.dumps(inner)}}]}


# A real LLM draft does NOT include an id — the prompt never asks for one and
# the server assigns it. The author must validate such a draft (regression: it
# used to require id and 422'd on every real draft).
_VALID_DRAFT = {
    "name": "Drafted restart",
    "match_rule": "*",
    "steps": [{"action": "restart"}],
    "hitl_mode": "hitl",
    "reversible": True,
}


def test_null_author_returns_none():
    assert NullRunbookAuthor().draft(_situation()) is None


@pytest.mark.parametrize(
    "headers,body,expected",
    [
        ({"retry-after": "4"}, {}, 4.0),  # header wins
        ({}, {"error": {"message": "Please try again in 6.51s. Need more"}}, 6.51),
        ({}, {"error": {"message": "try again in 1m2.5s"}}, 62.5),  # minutes + seconds
        ({}, {"error": {"message": "no delay mentioned"}}, None),
        ({}, {}, None),  # nothing to parse
    ],
)
def test_parse_retry_after(headers, body, expected):
    assert _parse_retry_after(_FakeResp(429, body, headers=headers)) == expected


def test_valid_draft_returns_typed_playbook():
    client = _FakeClient(_FakeResp(200, _content(_VALID_DRAFT)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    result = author.draft(_situation())
    assert result is not None
    playbook, rationale = result
    assert playbook.steps[0].action == "restart"
    assert rationale == "because"


def test_draft_without_id_validates():
    # Regression: real gpt-oss/OpenAI drafts omit `id` (the prompt doesn't ask
    # for it; the server assigns it). The author must still return a typed
    # playbook — it used to fail Playbook.model_validate on the missing id and
    # return None for EVERY real draft, silently breaking the whole feature.
    assert "id" not in _VALID_DRAFT
    client = _FakeClient(_FakeResp(200, _content(_VALID_DRAFT)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    result = author.draft(_situation())
    assert result is not None
    playbook, _ = result
    assert playbook.steps[0].action == "restart"


def test_draft_with_author_supplied_id_still_validates():
    # Back-compat: if a model DOES emit an id, validation still succeeds (the
    # server overwrites it downstream regardless).
    draft = {**_VALID_DRAFT, "id": "model-supplied"}
    client = _FakeClient(_FakeResp(200, _content(draft)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    assert author.draft(_situation()) is not None


def test_unsafe_action_in_draft_returns_none():
    bad = {**_VALID_DRAFT, "steps": [{"action": "delete"}]}
    client = _FakeClient(_FakeResp(200, _content(bad)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    # even across all retry attempts, an out-of-catalog action is never accepted
    assert author.draft(_situation()) is None  # model_validate rejects "delete"


def test_retry_recovers_after_invalid_drafts():
    # A non-conforming draft (placeholder where an int is required) is a bad
    # roll, not a broken endpoint — the author retries and returns the first
    # draft that validates. Simulate two bad rolls then a good one.
    bad_draft = {**_VALID_DRAFT, "steps": [{"action": "scale", "replicas": "{{n}}"}]}
    seq = [
        _FakeResp(200, _content(bad_draft)),
        _FakeResp(200, _content(bad_draft)),
        _FakeResp(200, _content(_VALID_DRAFT)),
    ]
    client = _FakeClient(resp_sequence=seq)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    result = author.draft(_situation())
    assert result is not None
    assert client.calls == 3  # it kept trying until one validated


def test_retry_gives_up_after_max_attempts():
    # If every attempt is non-conforming, the author exhausts its budget and
    # returns None (never a bad playbook).
    bad_draft = {**_VALID_DRAFT, "steps": [{"action": "scale", "replicas": "{{n}}"}]}
    client = _FakeClient(_FakeResp(200, _content(bad_draft)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    assert author.draft(_situation()) is None
    assert client.calls == 3


def test_transport_error_does_not_retry():
    # A transport failure won't heal within the loop — fail fast, one call only.
    client = _FakeClient(raise_http=True)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    assert author.draft(_situation()) is None
    assert client.calls == 1


def test_other_non_200_does_not_retry():
    # A non-429 error status (e.g. 500) is terminal — retrying won't help.
    client = _FakeClient(_FakeResp(500, {}))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    assert author.draft(_situation()) is None
    assert client.calls == 1


def test_rate_limit_backs_off_then_retries(monkeypatch):
    # A 429 is recoverable: wait the advised delay, then retry. Simulate a 429
    # (with a body-advised delay) followed by a good draft.
    slept = []
    monkeypatch.setattr(
        "services.governance.adapters.runbook_author.time.sleep", lambda s: slept.append(s)
    )
    rate_limited = _FakeResp(
        429, {"error": {"message": "Rate limit reached ... Please try again in 3.2s."}}
    )
    seq = [rate_limited, _FakeResp(200, _content(_VALID_DRAFT))]
    client = _FakeClient(resp_sequence=seq)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    result = author.draft(_situation())
    assert result is not None
    assert client.calls == 2
    assert slept == [3.2]  # honored the body-advised delay


def test_rate_limit_uses_retry_after_header(monkeypatch):
    # The standard Retry-After header takes precedence over the body message.
    slept = []
    monkeypatch.setattr(
        "services.governance.adapters.runbook_author.time.sleep", lambda s: slept.append(s)
    )
    rate_limited = _FakeResp(
        429, {"error": {"message": "try again in 9s"}}, headers={"retry-after": "2"}
    )
    seq = [rate_limited, _FakeResp(200, _content(_VALID_DRAFT))]
    client = _FakeClient(resp_sequence=seq)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    assert author.draft(_situation()) is not None
    assert slept == [2.0]  # header wins over the body's 9s


def test_rate_limit_backoff_capped(monkeypatch):
    # An absurd advised delay is clamped to the max backoff, never slept in full.
    slept = []
    monkeypatch.setattr(
        "services.governance.adapters.runbook_author.time.sleep", lambda s: slept.append(s)
    )
    rate_limited = _FakeResp(429, {"error": {"message": "try again in 300s"}})
    seq = [rate_limited, _FakeResp(200, _content(_VALID_DRAFT))]
    client = _FakeClient(resp_sequence=seq)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client, max_attempts=3)
    assert author.draft(_situation()) is not None
    assert slept == [15.0]  # _MAX_RATE_LIMIT_BACKOFF_SECONDS


@pytest.mark.parametrize(
    "resp,raise_http",
    [
        (None, True),  # transport error
        (_FakeResp(500, {}), False),  # non-200
        (_FakeResp(200, None, raise_json=True), False),  # non-JSON
        (_FakeResp(200, {"choices": []}), False),  # missing content
        (
            _FakeResp(200, {"choices": [{"message": {"content": "not json at all"}}]}),
            False,
        ),  # content not JSON
    ],
)
def test_failure_paths_return_none_never_raise(resp, raise_http):
    client = _FakeClient(resp, raise_http=raise_http)
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    assert author.draft(_situation()) is None
