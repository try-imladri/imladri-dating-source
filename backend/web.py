"""Serve the production React build and the API on the same origin."""
from pathlib import Path

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from backend.server import app as api


class ReactBuild(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            route = Path(path)
            accept = Headers(scope=scope).get("accept", "*/*")
            page_request = "text/html" in accept or accept == "*/*"
            asset_folder = route.parts and route.parts[0] in {"static", "img"}
            if exc.status_code != 404 or route.suffix or asset_folder or not page_request:
                raise
            # BrowserRouter handles page routes. Requests for missing assets retain their 404.
            return await super().get_response("index.html", scope)


build = Path(__file__).resolve().parents[1] / "frontend" / "build"
frontend = Starlette(routes=[Mount("/", ReactBuild(directory=build, html=True))])


async def app(scope, receive, send):
    # FastAPI owns its full namespace, including method handling, redirects and 404s.
    root = scope.get("path", "").lstrip("/").partition("/")[0]
    if scope["type"] != "http" or root in {"api", "docs", "redoc", "openapi.json"}:
        await api(scope, receive, send)
    else:
        await frontend(scope, receive, send)
