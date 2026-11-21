import asyncio
import base64
import json

import httpx
import pytest
from fastapi import HTTPException

from backend import ai


@pytest.fixture
def mock_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-provider-key")
    client_type = httpx.AsyncClient

    def install(handler):
        def client(**kwargs):
            return client_type(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(ai.httpx, "AsyncClient", client)

    return install


def completion(content):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})


def test_plain_text(mock_provider):
    def handler(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-provider-key"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["messages"] == [{"role": "system", "content": "Be concise"}, {"role": "user", "content": "Hi"}]
        assert "response_format" not in body
        return completion("Hello!")

    mock_provider(handler)
    assert asyncio.run(ai.ai_text("Be concise", "Hi")) == "Hello!"


def test_screenshot_json_retries_blank_once(mock_provider):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test buffer").decode()
    replies = iter(["  ", '{"lines": []}'])

    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON" in body["messages"][0]["content"]
        assert body["messages"][1]["content"] == [
            {"type": "text", "text": "Read this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + png, "detail": "high"}},
        ]
        return completion(next(replies))

    mock_provider(handler)
    assert asyncio.run(ai.ai_json("Transcribe", "Read this", png)) == {"lines": []}


def test_invalid_screenshot_is_not_sent(mock_provider):
    mock_provider(lambda request: pytest.fail("unexpected upstream call"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai.ai_json("Transcribe", "Read this", "bad-data"))
    assert exc.value.status_code == 422


def test_unexpected_json_array_is_retried_as_an_object(mock_provider):
    replies = iter(['[]', '{"lines": []}'])
    mock_provider(lambda request: completion(next(replies)))
    assert asyncio.run(ai.ai_json("Transcribe", "Read this")) == {"lines": []}


def test_provider_failure_is_sanitized_and_not_retried(mock_provider):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(402, text="private upstream account detail")

    mock_provider(handler)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai.ai_json("System", "Prompt"))
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail
    assert len(calls) == 1


def test_missing_key_does_not_fall_back_to_emergent(monkeypatch, mock_provider):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setenv("EMERGENT_LLM_KEY", "legacy-fixture")
    mock_provider(lambda request: pytest.fail("unexpected upstream call"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ai.ai_json("System", "Prompt"))
    assert exc.value.status_code == 503

def test_budget_settlement_outage_preserves_the_completed_reply(monkeypatch, mock_provider):
    from unittest.mock import AsyncMock
    from pymongo.errors import AutoReconnect
    from backend import ops
    monkeypatch.setattr(ops, "reserve_tokens", AsyncMock(return_value=("today", 9000)))
    monkeypatch.setattr(ops, "settle_tokens", AsyncMock(side_effect=AutoReconnect("fixture")))
    calls = []
    def complete(request):
        calls.append(1)
        return completion('{"reply":"hello"}')
    mock_provider(complete)
    async def run():
        token = ops.request_budget_db.set(object())
        try:
            assert await ai.ai_json("Fixture", "Hello") == {"reply":"hello"}
        finally:
            ops.request_budget_db.reset(token)
    asyncio.run(run())
    assert calls == [1]
    assert ops.reserve_tokens.await_count == ops.settle_tokens.await_count == 1
