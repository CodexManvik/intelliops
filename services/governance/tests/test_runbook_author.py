import json
from datetime import UTC, datetime

import httpx
import pytest

from common.contracts import Situation, SituationStatus
from services.governance.adapters.runbook_author import (
    NullRunbookAuthor,
    OpenAICompatibleRunbookAuthor,
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
    def __init__(self, status_code=200, body=None, raise_json=False):
        self.status_code = status_code
        self._body = body
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._body


class _FakeClient:
    def __init__(self, resp=None, raise_http=False):
        self._resp = resp
        self._raise_http = raise_http

    def post(self, *a, **k):
        if self._raise_http:
            raise httpx.ConnectError("unreachable")
        return self._resp


def _content(playbook_json: dict, rationale="because") -> dict:
    # an OpenAI-chat-shaped body whose message content is the JSON draft
    inner = {"playbook": playbook_json, "rationale": rationale}
    return {"choices": [{"message": {"content": json.dumps(inner)}}]}


_VALID_DRAFT = {
    "id": "ignored-by-server",
    "name": "Drafted restart",
    "match_rule": "*",
    "steps": [{"action": "restart"}],
    "hitl_mode": "hitl",
    "reversible": True,
}


def test_null_author_returns_none():
    assert NullRunbookAuthor().draft(_situation()) is None


def test_valid_draft_returns_typed_playbook():
    client = _FakeClient(_FakeResp(200, _content(_VALID_DRAFT)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    result = author.draft(_situation())
    assert result is not None
    playbook, rationale = result
    assert playbook.steps[0].action == "restart"
    assert rationale == "because"


def test_unsafe_action_in_draft_returns_none():
    bad = {**_VALID_DRAFT, "steps": [{"action": "delete"}]}
    client = _FakeClient(_FakeResp(200, _content(bad)))
    author = OpenAICompatibleRunbookAuthor("http://x", "m", http_client=client)
    assert author.draft(_situation()) is None  # model_validate rejects "delete"


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
