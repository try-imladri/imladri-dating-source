"""Explicit application pause for a coordinated database migration.

Off by default. The API pause includes reads because existing GET handlers and
authentication/recovery can write. A protected operator, if added, belongs in
the outer standby wrapper BEFORE the application; its path is not a bypass.
This process-local setting alone is not a distributed migration write fence.
"""
import os

from starlette.responses import JSONResponse


ENVIRONMENT_KEY = "IMLADRI_DATABASE_MAINTENANCE"


def paused():
    """Return the explicit mode; reject misspellings instead of opening writes."""
    value = os.getenv(ENVIRONMENT_KEY)
    if value is None or (isinstance(value, str) and value in ("", "off")):
        return False
    if isinstance(value, str) and value == "paused":
        return True
    raise ValueError("Invalid database maintenance configuration.")


def startup_allowed():
    """Whether normal database initialization/recovery may run at startup."""
    return not paused()


def _api_path(path):
    return isinstance(path, str) and (path == "/api" or path.startswith("/api/"))


class DatabaseMaintenance:
    """Reject API work before reading its body while retaining readiness/static UI."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # The owner integrates startup_allowed() into normal startup tasks.
        # Lifespan must keep working so a paused service can expose readiness.
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        try:
            active = paused()
        except ValueError:
            active = True
        if not active:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1013,
                               "reason": "Database maintenance in progress."})
        if scope["type"] == "http" and _api_path(scope.get("path")):
            ready = scope.get("path") == "/api/ready" and scope.get("method") in ("GET", "HEAD")
            if not ready:
                return await JSONResponse({"detail": "The database is temporarily paused for maintenance."},
                    status_code=503, headers={"Retry-After": "30", "Cache-Control": "no-store"})(scope, receive, send)
        return await self.app(scope, receive, send)
