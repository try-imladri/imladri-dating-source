"""Operator check for the secret and attribution configuration of a running production API."""
import os

from fastapi import APIRouter, Request, Response

try:
    from . import ops
    from .message_cost_admin import key_config
except ImportError:
    import ops
    from message_cost_admin import key_config


def router(env):
    checks = APIRouter()

    @checks.get("/ops/deepseek-connection")
    async def connection(request: Request, response: Response):
        ops.authorize(request.headers.get("Authorization", ""))
        response.headers["Cache-Control"] = "private, no-store"
        fingerprint, label, dedicated = key_config(env)
        # This checks the running container's binding with a read-only account call.
        available = await ops.check_ai() if dedicated else False
        if not (dedicated and available):
            response.status_code = 503
        return {"dedicated": dedicated, "render": os.getenv("RENDER") == "true", "key_id": fingerprint if dedicated else None,
                "key_name": label if dedicated else None, "provider_available": available}

    return checks
