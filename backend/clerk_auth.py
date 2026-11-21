"""Clerk session authentication with native domain support and an optional FAPI relay."""

import base64
import binascii
import os
from functools import lru_cache
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import urlsplit

import jwt
import requests
from fastapi import APIRouter, HTTPException, Request, Response
from requests.adapters import HTTPAdapter
from starlette.concurrency import run_in_threadpool
from urllib3.util.retry import Retry

CLERK_FRONTEND_API = "https://frontend-api.clerk.dev"
CLERK_BACKEND_API = "https://api.clerk.com/v1"
PROXY_PATH = "/api/__clerk"

# Per-hop headers are never relayed; requests decodes the body, so its encoding goes too.
_HOP_HEADERS = {
    "host", "connection", "keep-alive", "content-length", "transfer-encoding",
    "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
    "content-encoding",
}

clerk_router = APIRouter(prefix="/api")


class _RelayCookiePolicy(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        # Each browser supplies its own Cookie header. Pool connections only.
        return False


_http = requests.Session()
_http.cookies.set_policy(_RelayCookiePolicy())
# Clerk's edge closes idle keep-alive connections; retry idempotent calls on a dropped one.
_retry = Retry(total=2, connect=2, read=2, status=0, backoff_factor=0.1,
               allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}))
_http.mount("https://", HTTPAdapter(pool_maxsize=40, max_retries=_retry))  # matches the threadpool size


def _publishable_key() -> str:
    return os.environ.get("CLERK_PUBLISHABLE_KEY", "")


def _secret_key() -> str:
    return os.environ.get("CLERK_SECRET_KEY", "")


def _is_production() -> bool:
    return _publishable_key().startswith("pk_live_")


def _https_origin(value: str) -> str:
    parts = urlsplit(value.strip())
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.path not in ("", "/") or parts.query or parts.fragment or "*" in parts.netloc):
        raise ValueError("Expected an HTTPS origin")
    return f"https://{parts.netloc.lower()}"


def _production_issuer() -> str:
    encoded = _publishable_key().removeprefix("pk_live_")
    try:
        value = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("ascii")
        if not value.endswith("$"):
            raise ValueError("Invalid publishable key format")
        return _https_origin("https://" + value[:-1])
    except (ValueError, UnicodeError, binascii.Error):
        raise HTTPException(503, "Clerk production domain is not configured correctly.") from None


def _authorized_parties(issuer: str) -> set[str]:
    origins = [p.strip() for p in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",") if p.strip()]
    if site := os.getenv("PUBLIC_APP_URL", "").strip():
        origins.append(site)
    if not origins and issuer.startswith("https://clerk."):
        origins.append("https://" + issuer.removeprefix("https://clerk."))
    try:
        if not origins:
            raise ValueError("No application origin")
        return {_https_origin(origin) for origin in origins}
    except ValueError:
        raise HTTPException(503, "Clerk application origins are not configured correctly.") from None


def _proxy_url() -> str:
    url = os.getenv("CLERK_PROXY_URL", "").strip().rstrip("/")
    if url and _is_production():
        try:
            if not url.endswith(PROXY_PATH):
                raise ValueError("Expected the configured relay path")
            return _https_origin(url[:-len(PROXY_PATH)]) + PROXY_PATH
        except ValueError:
            raise HTTPException(503, "Clerk proxy URL is not configured correctly.") from None
    return ""


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


@clerk_router.get("/clerk/config")
def clerk_config():
    """What the frontend needs to start Clerk (the publishable key is public)."""
    key = _publishable_key()
    return {
        "enabled": bool(key and _secret_key()),
        "publishable_key": key,
        "proxy_url": _proxy_url(),
    }


@clerk_router.api_route("/__clerk/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def clerk_proxy(path: str, request: Request):
    """Relay Clerk's Frontend API through this app's own domain."""
    proxy_url = _proxy_url()
    if not proxy_url:
        raise HTTPException(status_code=404)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    headers["Clerk-Proxy-Url"] = proxy_url
    headers["Clerk-Secret-Key"] = _secret_key()
    headers["X-Forwarded-For"] = _client_ip(request)
    headers["Accept-Encoding"] = "gzip"  # requests cannot decode brotli

    try:
        upstream = await run_in_threadpool(
            _http.request,
            request.method,
            f"{CLERK_FRONTEND_API}/{path}",
            params=list(request.query_params.multi_items()),
            headers=headers,
            data=await request.body(),
            allow_redirects=False,
            timeout=30,
        )
    except requests.RequestException:
        # Clerk's frontend retries a 502; an unhandled error would be a bare 500.
        return Response(status_code=502)
    response = Response(content=upstream.content, status_code=upstream.status_code)
    raw = upstream.raw.headers
    for name in {n.lower() for n in raw.keys()} - _HOP_HEADERS:
        # getlist keeps every Set-Cookie separate; joining them breaks sessions.
        for value in raw.getlist(name):
            response.raw_headers.append((name.encode(), value.encode()))
    return response


@lru_cache(maxsize=1)
def _jwks_client(secret_key: str) -> jwt.PyJWKClient:
    # Caches Clerk's signing keys and refetches when it meets a new key id.
    return jwt.PyJWKClient(
        f"{CLERK_BACKEND_API}/jwks",
        # Clerk's edge rejects urllib's default User-Agent (Cloudflare 1010).
        headers={"Authorization": f"Bearer {secret_key}", "User-Agent": "imladri-clerk-auth"},
        lifespan=3600,
        timeout=10,
    )


def verify_session_token(token: str) -> dict:
    """Return the verified claims of a Clerk session token, or raise 401."""
    try:
        key = _jwks_client(_secret_key()).get_signing_key_from_jwt(token).key
        issuer = _production_issuer() if _is_production() else None
        claims = jwt.decode(token, key=key, algorithms=["RS256"], issuer=issuer, leeway=5,
                            options={"require": ["exp", "iat", "sub", "sid"]})
        if not claims["sub"] or not isinstance(claims["sid"], str) or not claims["sid"] or claims.get("sts") == "pending":
            raise jwt.InvalidTokenError("An active user session is required")
        if issuer and (not isinstance(claims.get("azp"), str) or claims["azp"] not in _authorized_parties(issuer)):
            raise jwt.InvalidTokenError("Unexpected authorized party")
        return claims
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="Sign-in verification is temporarily unavailable") from None
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None


def require_user(request: Request) -> dict:
    """FastAPI dependency: the signed-in user from `Authorization: Bearer`
    (frontend getToken()) or Clerk's `__session` cookie; user["user_id"] is stable."""
    if not _secret_key():
        raise HTTPException(status_code=503, detail="Clerk is not configured")
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get("__session", "")
    if not token:
        raise HTTPException(status_code=401, detail="Not signed in")
    claims = verify_session_token(token)
    return {"user_id": claims["sub"], "session_id": claims.get("sid"), "claims": claims}
