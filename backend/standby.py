"""Serve the live Render fallback only to the Cloudflare edge."""
import hmac
import os

from starlette.requests import Request
from starlette.responses import Response

from . import standby_ingress
from .server import app as personal_api, db


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        from .postgres_operator_api import shutdown

        async def lifecycle_receive():
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                # Finish or terminate the bounded maintenance child before the
                # application closes its database connections.
                await shutdown()
            return message

        try:
            return await personal_api(scope, lifecycle_receive, send)
        finally:
            await shutdown()
    if scope["type"] != "http":
        return await send({"type": "websocket.close", "code": 1008})
    if scope["path"] == "/api/ready" and scope["method"] in ("GET", "HEAD"):
        return await personal_api(scope, receive, send)
    expected = os.environ.get("RENDER_ORIGIN_SECRET", "").encode()
    supplied = [value for field, value in scope["headers"] if field.lower() == b"x-imladri-origin"]
    if not expected or len(supplied) != 1 or not hmac.compare_digest(supplied[0], expected):
        return await Response(status_code=404, headers={"Cache-Control": "no-store"})(scope, receive, send)
    if scope["path"] == "/api/internal/database-migration":
        # The maintenance middleware deliberately blocks ordinary API work.
        # This fixed endpoint has its own exact paused-standby/release gate,
        # origin check and separate bearer secret before reading the body.
        from .postgres_operator_api import endpoint
        response = await endpoint(Request(scope, receive=receive))
        return await response(scope, receive, send)
    if scope["path"] == "/api/internal/postgres":
        # Data-plane capability is absent until the explicit datastore switch.
        # Both the existing origin gate and dedicated signed envelope are needed.
        from .database_maintenance import startup_allowed
        if not startup_allowed():
            return await Response(status_code=503, headers={"Retry-After": "30", "Cache-Control": "no-store"})(scope, receive, send)
        if getattr(type(db), "rpc", None) is None or scope["method"] != "POST":
            return await Response(status_code=404, headers={"Cache-Control": "no-store"})(scope, receive, send)
        from .postgres_gateway import endpoint
        response = await endpoint(Request(scope, receive=receive), db)
        return await response(scope, receive, send)
    if scope["path"] == "/api/ops/repair-ingress":
        response = await standby_ingress.endpoint(Request(scope, receive=receive), db)
        return await response(scope, receive, send)
    return await personal_api(scope, receive, send)
