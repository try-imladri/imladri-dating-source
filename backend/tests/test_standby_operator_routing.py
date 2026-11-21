"""The paused standby exposes only the separately authenticated operator."""
import asyncio
from uuid import uuid4

import httpx
import pytest

from backend import postgres_operator_api as operator
from backend import standby
from backend.postgres_operator import SERVICE_ID, SERVICE_NAME


@pytest.mark.parametrize("change,expected", [({}, 202),
    ({"IMLADRI_DATABASE_MAINTENANCE": "off"}, 404),
    ({"RENDER_SERVICE_ID": "srv-preview"}, 404),
    ({"RENDER_SERVICE_NAME": "imladri-personal-pilot"}, 404)])
def test_only_paused_exact_standby_can_route_operator(monkeypatch, change, expected):
    env = {"RENDER_SERVICE_ID": SERVICE_ID, "RENDER_SERVICE_NAME": SERVICE_NAME,
        "RENDER_GIT_COMMIT": "a" * 40, "IMLADRI_DATABASE_MAINTENANCE": "paused",
        "IMLADRI_DATABASE_OPERATOR_SECRET": "fixture-operator-" * 3,
        "RENDER_ORIGIN_SECRET": "fixture-origin-" * 3,
        "IMLADRI_POSTGRES_GATEWAY_SECRET": "fixture-gateway-" * 3, **change}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = []

    async def runner(envelope, environ):
        calls.append(envelope)
        return {"version": 1, "status": "completed"}

    async def application(*args):
        raise AssertionError("operator reached ordinary application middleware")

    controller = operator.OperatorController(env, runner=runner)
    monkeypatch.setattr(operator, "_controller", controller)
    monkeypatch.setattr(standby, "personal_api", application)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=standby.app), base_url="http://fixture") as client:
            path = operator.PATH
            origin = {"X-Imladri-Origin": env["RENDER_ORIGIN_SECRET"]}
            body = {"command": "inspect", "operation_id": str(uuid4()), "expected_release_sha": "a" * 40}
            assert (await client.post(path, json=body)).status_code == 404
            assert (await client.post(path, headers=origin, json=body)).status_code == 404
            result = await client.post(path, headers={**origin,
                "Authorization": "Bearer " + env["IMLADRI_DATABASE_OPERATOR_SECRET"]}, json=body)
            assert result.status_code == expected
            assert result.headers["cache-control"] == "no-store"
            await asyncio.sleep(0)
            assert len(calls) == (1 if expected == 202 else 0)
        await controller.shutdown()

    asyncio.run(exercise())


def test_shutdown_reaps_operator_before_application_shutdown(monkeypatch):
    events = []

    async def shutdown():
        events.append("operator-cleaned")

    async def application(scope, receive, send):
        assert scope["type"] == "lifespan"
        assert (await receive())["type"] == "lifespan.shutdown"
        events.append("application-cleaned")

    async def receive():
        return {"type": "lifespan.shutdown"}

    async def send(message):
        raise AssertionError("unexpected response")

    monkeypatch.setattr(operator, "shutdown", shutdown)
    monkeypatch.setattr(standby, "personal_api", application)
    asyncio.run(standby.app({"type": "lifespan"}, receive, send))
    assert events == ["operator-cleaned", "application-cleaned", "operator-cleaned"]
