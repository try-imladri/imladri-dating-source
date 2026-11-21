"""The pause fails closed, has no internal pathname bypass, and reads no body."""
import asyncio
import json

import pytest

from backend import database_maintenance as maintenance


@pytest.mark.parametrize("mode", [None, "", "off"])
def test_default_and_explicit_off_allow_startup(monkeypatch, mode):
    if mode is None:
        monkeypatch.delenv(maintenance.ENVIRONMENT_KEY, raising=False)
    else:
        monkeypatch.setenv(maintenance.ENVIRONMENT_KEY, mode)
    assert maintenance.paused() is False
    assert maintenance.startup_allowed() is True


def test_explicit_pause_prevents_normal_startup(monkeypatch):
    monkeypatch.setenv(maintenance.ENVIRONMENT_KEY, "paused")
    assert maintenance.paused() is True
    assert maintenance.startup_allowed() is False


@pytest.mark.parametrize("mode", ["false", "true", "0", "1", "PAUSED", " paused ", "pause", "OFF", True, 1, [], {}])
def test_invalid_mode_cannot_silently_enable_startup(monkeypatch, mode):
    monkeypatch.setattr(maintenance.os, "getenv", lambda name: mode)
    with pytest.raises(ValueError, match="Invalid database maintenance configuration"):
        maintenance.paused()
    with pytest.raises(ValueError):
        maintenance.startup_allowed()


async def invoke(scope, mode, monkeypatch):
    if mode is None:
        monkeypatch.delenv(maintenance.ENVIRONMENT_KEY, raising=False)
    else:
        monkeypatch.setenv(maintenance.ENVIRONMENT_KEY, mode)
    called, sent, reads = [], [], []
    async def app(scope, receive, send):
        called.append(scope)
    async def receive():
        reads.append(True)
        raise AssertionError("Pause middleware read a request body or socket frame")
    async def send(message):
        sent.append(message)
    await maintenance.DatabaseMaintenance(app)(scope, receive, send)
    return called, sent, reads


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("path", ["/api", "/api/", "/api/me", "/api/webhooks/stripe", "/api/ops/check",
                                   "/api/internal/database-migration", "/api/internal/postgres"])
def test_paused_blocks_every_api_method_before_receive_including_get_and_internal_paths(monkeypatch, method, path):
    scope = {"type": "http", "path": path, "method": method, "headers": []}
    called, sent, reads = asyncio.run(invoke(scope, "paused", monkeypatch))
    assert not called and not reads
    assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]
    assert sent[0]["status"] == 503
    headers = dict(sent[0]["headers"])
    assert headers[b"retry-after"] == b"30" and headers[b"cache-control"] == b"no-store"
    assert json.loads(sent[1]["body"])["detail"] == "The database is temporarily paused for maintenance."


@pytest.mark.parametrize("mode", [None, "off"])
def test_off_preserves_api_request_without_reading_it(monkeypatch, mode):
    scope = {"type": "http", "path": "/api/webhooks/stripe", "method": "POST", "headers": []}
    called, sent, reads = asyncio.run(invoke(scope, mode, monkeypatch))
    assert called == [scope] and not sent and not reads


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_exact_readiness_remains_accessible_while_paused(monkeypatch, method):
    scope = {"type": "http", "path": "/api/ready", "method": method, "headers": []}
    called, sent, reads = asyncio.run(invoke(scope, "paused", monkeypatch))
    assert called == [scope] and not sent and not reads


@pytest.mark.parametrize("path,method", [("/api/ready", "POST"), ("/api/ready", "OPTIONS"),
                                         ("/api/ready/", "GET"), ("/api/ready/anything", "GET")])
def test_readiness_exception_cannot_expand_to_writing_methods_or_related_paths(monkeypatch, path, method):
    called, sent, reads = asyncio.run(invoke({"type": "http", "path": path, "method": method}, "paused", monkeypatch))
    assert not called and not reads and sent[0]["status"] == 503


@pytest.mark.parametrize("path", ["/", "/index.html", "/assets/app.js", "/plans", "/apiary"])
def test_static_and_non_api_http_are_unchanged(monkeypatch, path):
    scope = {"type": "http", "path": path, "method": "GET"}
    called, sent, reads = asyncio.run(invoke(scope, "paused", monkeypatch))
    assert called == [scope] and not sent and not reads


@pytest.mark.parametrize("path", ["/api/live", "/ws", "/api/internal/database-migration"])
def test_paused_app_websockets_close_without_accepting_or_reading_a_frame(monkeypatch, path):
    called, sent, reads = asyncio.run(invoke({"type": "websocket", "path": path}, "paused", monkeypatch))
    assert not called and not reads
    assert sent == [{"type": "websocket.close", "code": 1013, "reason": "Database maintenance in progress."}]


def test_off_websocket_and_paused_lifespan_are_forwarded(monkeypatch):
    socket = {"type": "websocket", "path": "/ws"}
    assert asyncio.run(invoke(socket, "off", monkeypatch))[0] == [socket]
    lifespan = {"type": "lifespan"}
    called, sent, reads = asyncio.run(invoke(lifespan, "paused", monkeypatch))
    assert called == [lifespan] and not sent and not reads


def test_misspelled_runtime_mode_is_still_fail_closed_without_echoing_configuration(monkeypatch):
    called, sent, reads = asyncio.run(invoke({"type": "http", "path": "/api/me", "method": "GET"},
        "private-invalid-configuration", monkeypatch))
    assert not called and not reads and sent[0]["status"] == 503
    assert b"private-invalid-configuration" not in sent[1]["body"]
