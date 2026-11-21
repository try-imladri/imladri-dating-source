import asyncio
import hashlib
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from backend import deepseek_connection, ops


def test_operator_can_verify_binding_without_a_completion_or_customer_session(monkeypatch):
    key = "fixture-credential"
    monkeypatch.setenv("OPS_CHECK_SECRET", "fixture-ops")
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_NAME", "fixture-label")
    health = AsyncMock(return_value=True)
    monkeypatch.setattr(ops, "check_ai", health)

    async def check():
        app = FastAPI()
        app.include_router(deepseek_connection.router("live"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
            path="/ops/deepseek-connection"
            assert (await client.get(path)).status_code == 401
            client.headers["Authorization"] = "Bearer fixture-ops"
            monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_ID", "incorrect")
            bad=await client.get(path)
            assert bad.status_code==503 and bad.json()["key_id"] is None
            health.assert_not_awaited()
            fingerprint=hashlib.sha256(key.encode()).hexdigest()[:24]
            monkeypatch.setenv("DEEPSEEK_PRODUCTION_KEY_ID", fingerprint)
            good=await client.get(path)
            assert good.status_code == 200 and good.json()["key_id"] == fingerprint
            assert good.json()["dedicated"] and good.headers["cache-control"] == "private, no-store"
            assert key not in good.text
            health.assert_awaited_once()
    asyncio.run(check())
