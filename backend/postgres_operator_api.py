"""Dedicated paused-standby operator endpoint and bounded fixed-child supervisor."""
import asyncio
from collections import OrderedDict
import hmac
import json
import os
import signal
import sys

from starlette.responses import JSONResponse

from .postgres_operator import (MAX_BODY, MAX_OUTPUT, MAX_SECONDS, ROOT, OperatorBlocked, child_environment,
                                decode_json, deployment_gate, digest, safe_result, validate_request)


PATH = "/api/internal/database-migration"
NO_STORE = {"Cache-Control": "no-store"}


def authenticate(request, environ):
    deployment_gate(environ)
    secret = environ.get("IMLADRI_DATABASE_OPERATOR_SECRET", "")
    origin = environ.get("RENDER_ORIGIN_SECRET", "")
    if (not isinstance(secret, str) or not 32 <= len(secret.encode()) <= 256 or not origin
            or not isinstance(origin, str) or hmac.compare_digest(secret.encode(), origin.encode())
            or secret == environ.get("IMLADRI_POSTGRES_GATEWAY_SECRET")):
        raise OperatorBlocked("operator_authentication_unavailable")
    headers = request.scope.get("headers", [])
    supplied = [value for key, value in headers if key.lower() == b"authorization"]
    origins = [value for key, value in headers if key.lower() == b"x-imladri-origin"]
    if (len(supplied) != 1 or len(origins) != 1 or not hmac.compare_digest(supplied[0], ("Bearer " + secret).encode())
            or not hmac.compare_digest(origins[0], origin.encode())):
        raise OperatorBlocked("operator_authentication_failed")


def _safe_output(value):
    """Defense in depth: count/proof JSON must not contain raw credentials/data."""
    return safe_result(value)


def checked_result(data):
    try:
        result = decode_json(data, maximum=MAX_OUTPUT)
        if (not isinstance(result, dict) or result.get("version") != 1
                or result.get("status") not in {"completed", "not_found", "running", "unknown", "blocked"}
                or not _safe_output(result)):
            raise OperatorBlocked("invalid_worker_result")
        security = result.get("security")
        if result["status"] != "blocked" and (not isinstance(security, dict)
                or any(security.get(key) != "0000000000000000" for key in ("CapEff", "CapPrm", "CapInh"))
                or security.get("NoNewPrivs") != "1"):
            raise OperatorBlocked("worker_hardening_not_verified")
        return result
    except (OperatorBlocked, ValueError, TypeError):
        return {"version": 1, "status": "blocked", "error_type": "invalid_worker_result", "needs_manual_review": True}


async def _kill_and_reap(process):
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except ProcessLookupError:
                pass
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        pass


async def run_child(envelope, environ, *, process_factory=asyncio.create_subprocess_exec):
    """Fixed Python module, no shell; no inherited AI/billing/identity credentials."""
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_BODY:
        raise OperatorBlocked("request_too_large")
    process = None
    spawn = None
    try:
        async with asyncio.timeout(MAX_SECONDS):
            spawn = asyncio.create_task(process_factory(sys.executable, "-m", "backend.postgres_operator", cwd=str(ROOT),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=child_environment(environ), start_new_session=True))
            process = await asyncio.shield(spawn)
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > MAX_OUTPUT:
                    await _kill_and_reap(process)
                    return {"version": 1, "status": "unknown", "error_type": "worker_output_bound", "needs_manual_review": True}
            code = await process.wait()
            result = checked_result(bytes(output))
            if code != 0 and result.get("status") != "blocked":
                return {"version": 1, "status": "unknown", "error_type": "worker_exit_unverified", "needs_manual_review": True}
            return result
    except (TimeoutError, asyncio.CancelledError):
        if process is not None:
            await _kill_and_reap(process)
        elif spawn is not None and spawn.done():
            try:
                created = spawn.result()
            except BaseException:
                created = None
            if created is not None:
                await _kill_and_reap(created)
        elif spawn is not None:
            def cleanup(future):
                try:
                    late_process = future.result()
                except BaseException:
                    return
                asyncio.create_task(_kill_and_reap(late_process))
            spawn.add_done_callback(cleanup)
        return {"version": 1, "status": "unknown", "error_type": "worker_deadline_or_cancellation", "needs_manual_review": True}
    except Exception:
        if process is not None:
            await _kill_and_reap(process)
        return {"version": 1, "status": "unknown", "error_type": "worker_transport_failed", "needs_manual_review": True}


class OperatorController:
    def __init__(self, environ=None, *, runner=run_child):
        self.environ = os.environ if environ is None else environ
        self.runner = runner
        self.operations = OrderedDict()
        self.lock = asyncio.Lock()

    async def submit(self, request):
        validated = validate_request(request, self.environ)
        identity = validated["operation_id"]
        async with self.lock:
            existing = self.operations.get(identity)
            if existing:
                if existing["request_sha256"] != digest(validated):
                    raise OperatorBlocked("operation_id_reused_for_different_request")
                return self._local(identity, existing)
            if any(not record["task"].done() for record in self.operations.values()):
                raise OperatorBlocked("another_operator_step_is_running")
            while len(self.operations) >= 32:
                self.operations.popitem(last=False)
            task = asyncio.create_task(self._run({"action": "execute", "request": validated}))
            record = {"request_sha256": digest(validated), "task": task,
                      "expected_release_sha": validated["expected_release_sha"]}
            self.operations[identity] = record
            return self._local(identity, record)

    def _local(self, identity, record):
        if record["task"].done():
            try:
                return record["task"].result()
            except BaseException:
                return {"version": 1, "status": "unknown", "operation_id": identity, "needs_manual_review": True}
        return {"version": 1, "status": "running", "operation_id": identity,
                "local_child_pending_or_active": True, "needs_manual_review": False}

    async def status(self, identity):
        validated = validate_request({"operation_id": identity, "expected_release_sha": deployment_gate(self.environ)},
                                     self.environ, status=True)
        record = self.operations.get(validated["operation_id"])
        if record:
            if record["expected_release_sha"] != validated["expected_release_sha"]:
                raise OperatorBlocked("operation_belongs_to_previous_release")
            local = self._local(validated["operation_id"], record)
            if local.get("status") in {"running", "completed"}:
                return local
        # A lost pipe/parent acknowledgement may follow a durable completion.
        # Resolve it through SELECT-only status; never repeat the business step.
        durable = await self._run({"action": "status", "request": validated})
        # A validation block before reserving an intent has no durable row.
        # Preserve its sanitized reason instead of losing it to "not_found".
        # Durable completion still takes precedence after an ambiguous pipe.
        if record and local.get("status") == "blocked" and durable.get("status") == "not_found":
            return local
        return durable

    async def _run(self, envelope):
        try:
            return await self.runner(envelope, self.environ)
        except BaseException:
            return {"version": 1, "status": "unknown", "error_type": "worker_transport_failed", "needs_manual_review": True}

    async def shutdown(self):
        tasks = [record["task"] for record in self.operations.values() if not record["task"].done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_controller = OperatorController()


async def shutdown():
    await _controller.shutdown()


async def endpoint(request, controller=None):
    controller = controller or _controller
    try:
        if request.url.path != PATH:
            raise OperatorBlocked("unknown_operator_path")
        authenticate(request, controller.environ)
    except (OperatorBlocked, ValueError):
        return JSONResponse({"detail": "Not found"}, status_code=404, headers=NO_STORE)
    try:
        if request.method == "POST":
            if request.query_params:
                raise OperatorBlocked("unexpected_query_parameters")
            length = request.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > MAX_BODY):
                return JSONResponse({"detail": "Request too large"}, status_code=413, headers=NO_STORE)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    return JSONResponse({"detail": "Request too large"}, status_code=413, headers=NO_STORE)
            result = await controller.submit(decode_json(bytes(body)))
            return JSONResponse(result, status_code=202 if result["status"] == "running" else 200, headers=NO_STORE)
        if request.method == "GET":
            values = list(request.query_params.multi_items())
            if len(values) != 1 or values[0][0] != "operation_id":
                raise OperatorBlocked("operation_identity_required")
            return JSONResponse(await controller.status(values[0][1]), headers=NO_STORE)
        return JSONResponse({"detail": "Method not allowed"}, status_code=405, headers={**NO_STORE, "Allow": "GET, POST"})
    except OperatorBlocked as error:
        return JSONResponse({"detail": str(error), "needs_manual_review": False}, status_code=409, headers=NO_STORE)
    except Exception:
        return JSONResponse({"detail": "Operator unavailable", "needs_manual_review": True}, status_code=503, headers=NO_STORE)
