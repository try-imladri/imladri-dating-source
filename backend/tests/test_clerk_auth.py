import base64
from email.message import Message
from types import SimpleNamespace
from time import time
from urllib.request import Request

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import clerk_auth


@pytest.fixture
def production(monkeypatch):
    fapi = "clerk.example.test"
    public_key = "pk_live_" + base64.b64encode((fapi + "$").encode()).decode().rstrip("=")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", public_key)
    monkeypatch.setenv("CLERK_SECRET_KEY", "unused-fixture")
    monkeypatch.setenv("PUBLIC_APP_URL", "https://personal.example.test")
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)
    monkeypatch.delenv("CLERK_PROXY_URL", raising=False)
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=signing_key.public_key()))
    monkeypatch.setattr(clerk_auth, "_jwks_client", lambda _: keys)
    now = int(time())
    claims = {"iss": "https://" + fapi, "azp": "https://personal.example.test", "sub": "user_fixture",
              "sid": "sess_fixture", "iat": now, "nbf": now, "exp": now + 60}

    def sign(changes=None):
        data = {**claims, **(changes or {})}
        return jwt.encode({k: v for k, v in data.items() if v is not None}, signing_key, algorithm="RS256")

    return sign


def test_native_production_and_opt_in_proxy(production, monkeypatch):
    app = FastAPI()
    app.include_router(clerk_auth.clerk_router)
    with TestClient(app) as client:
        config = client.get("/api/clerk/config").json()
        assert config["enabled"] is True and config["proxy_url"] == ""
        assert client.get("/api/__clerk/v1/environment").status_code == 404
        monkeypatch.setenv("CLERK_PROXY_URL", "https://personal.example.test/api/__clerk")
        assert client.get("/api/clerk/config").json()["proxy_url"] == "https://personal.example.test/api/__clerk"


def test_live_personal_origin_and_active_session(production, monkeypatch):
    assert clerk_auth.verify_session_token(production())["sub"] == "user_fixture"
    for change in ({"iss": "https://other-clerk.example.test"}, {"azp": "https://market.example.test"},
                   {"azp": None}, {"sid": None}, {"sts": "pending"}, {"exp": int(time()) - 60}):
        with pytest.raises(HTTPException) as rejected:
            clerk_auth.verify_session_token(production(change))
        assert rejected.value.status_code == 401
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "https://preview.personal.example.test")
    assert clerk_auth.verify_session_token(production({"azp": "https://preview.personal.example.test"}))["sid"] == "sess_fixture"


def test_jwks_outage_is_retryable(monkeypatch):
    def unavailable(_):
        raise jwt.PyJWKClientConnectionError("simulated outage")
    monkeypatch.setattr(clerk_auth, "_jwks_client", unavailable)
    with pytest.raises(HTTPException) as rejected:
        clerk_auth.verify_session_token("fixture")
    assert rejected.value.status_code == 503


def test_relay_does_not_share_browser_cookies():
    headers = Message()
    headers.add_header("Set-Cookie", "relay_fixture=one-browser; Path=/; Secure")
    clerk_auth._http.cookies.extract_cookies(SimpleNamespace(info=lambda: headers),
                                            Request("https://frontend-api.clerk.dev/v1/client"))
    assert clerk_auth._http.cookies.get("relay_fixture") is None
