"""Drop Linux privileges before diagnostics or application datastore access."""
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import platform
import sys
import time
import urllib.request


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]


class _CapabilityData(ctypes.Structure):
    _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                ('inheritable', ctypes.c_uint32)]


def _security_state():
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    return {name: fields[name].strip() for name in ('CapEff', 'CapPrm', 'CapInh', 'NoNewPrivs')}


def harden_process():
    """Irreversibly remove capabilities for this thread and its exec descendants.

    The provider can grant Linux capabilities despite a non-root Docker USER.
    Fail before any networking if the kernel cannot enforce the required state.
    """
    if sys.platform != 'linux':
        raise RuntimeError('Linux process hardening requires Linux')
    architecture = platform.machine().lower()
    numbers = {'x86_64': 126, 'amd64': 126, 'aarch64': 91, 'arm64': 91}
    if architecture not in numbers:
        raise RuntimeError('Unsupported architecture for Linux capability clearing')
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    syscall.argtypes = [ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    header = _CapabilityHeader(0x20080522, 0)
    data = (_CapabilityData * 2)()
    if syscall(numbers[architecture], ctypes.byref(header), ctypes.byref(data)) != 0:
        raise OSError(ctypes.get_errno(), 'Linux capability clearing failed')
    prctl = libc.prctl
    prctl.restype = ctypes.c_int
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    if prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Linux no-new-privileges enforcement failed')
    state = _security_state()
    if any(int(state[name], 16) != 0 for name in ('CapEff', 'CapPrm', 'CapInh')) or state['NoNewPrivs'] != '1':
        raise RuntimeError('Linux process hardening postcondition failed')
    return state


def observe_egress():
    # api.ipify.org is IPv4-only. Bypass environment-configured HTTP proxies
    # and use Python's default certificate and hostname verification.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("https://api.ipify.org?format=json", timeout=6) as response:
        address = ipaddress.IPv4Address(json.loads(response.read(96))["ip"])
        if not address.is_global:
            raise ValueError("Expected a public IPv4 address")
    return address


def wait_for_database():
    if os.environ.get("IMLADRI_DATABASE_MAINTENANCE") == "paused":
        # The paused process serves only explicit maintenance readiness, with no
        # source initialization or background writers. No database fallback.
        return
    if os.environ.get("IMLADRI_DATABASE_BACKEND", "mongodb") != "mongodb":
        import asyncio
        sys.path.insert(0, "/app")
        from backend.database import open_database
        async def probe():
            client, db = open_database()
            try:
                await db.command("ping")
            finally:
                await client.aclose()
        # A target failure never silently reconnects the retired source.
        for attempt in range(12):
            try:
                asyncio.run(probe())
                return
            except Exception:
                if attempt == 11:
                    raise RuntimeError("Application datastore readiness failed.") from None
                time.sleep(3)
        return
    # Keep the same container and source address while the standby registers
    # its narrow Atlas ingress rule. A failed FastAPI startup would exit.
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    while True:
        try:
            with MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=4000, connectTimeoutMS=3000) as client:
                client.admin.command("ping")
            return
        except PyMongoError:
            time.sleep(3)


def main():
    harden_process()
    from backend.native_patch_manifest import read_native_patch_manifest
    read_native_patch_manifest()
    if os.getenv("CF_DIAG_EGRESS_ON_START") == "true":
        try:
            print(f"imladri_container_egress_ipv4={observe_egress()}", flush=True)
        except (OSError, KeyError, TypeError, ValueError):
            print("imladri_container_egress_ipv4=unavailable", flush=True)
    wait_for_database()
    from backend.runtime_manifest import emit_runtime_manifest
    if not emit_runtime_manifest():
        raise RuntimeError('Runtime inventory verification failed')
    os.execvp("python", [
        "python", "-m", "uvicorn", "backend.server:app", "--host", "0.0.0.0",
        "--port", "8080", "--no-access-log",
    ])


if __name__ == '__main__':
    main()
