"""Launch either Render service with its assigned port and without shell parsing."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


APPS = {"preview": "backend.web:app", "standby": "backend.standby:app"}
KERNEL_PREFIX = "IMLADRI_MAIN_KERNEL_v1 "
RUNTIME_PREFIX = "IMLADRI_RUNTIME_FINGERPRINT_v2 "


def _validate_security(state):
    if (not isinstance(state, dict) or set(state) != {"CapEff", "CapPrm", "CapInh", "NoNewPrivs"}
            or any(state[key] != "0000000000000000" for key in ("CapEff", "CapPrm", "CapInh"))
            or state["NoNewPrivs"] != "1"):
        raise RuntimeError("Render main process hardening was not enforced by the kernel.")
    return state


def _harden():
    # This fixed image file is also the Cloudflare entrypoint. Its module uses
    # only the standard library; no application or third-party imports precede
    # its capability clearing and kernel-enforced no-new-privileges check.
    spec = importlib.util.spec_from_file_location("imladri_render_hardener", "/app/entrypoint.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("The trusted image hardener is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _validate_security(module.harden_process())


def _kernel_state():
    fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    return _validate_security({key: fields[key].strip() for key in ("CapEff", "CapPrm", "CapInh", "NoNewPrivs")})


def _runtime_manifest():
    from backend.runtime_manifest import runtime_manifest
    return runtime_manifest()


def _attest(role, source, security):
    manifest = _runtime_manifest()
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 2 or "attestation_error" in manifest
            or manifest.get("effective_capabilities") != "0000000000000000"):
        raise RuntimeError("The main process runtime fingerprint is invalid.")
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)
    current = _kernel_state()
    if current != security:
        raise RuntimeError("The main process kernel state changed before launch.")
    proof = {"source": source, "role": role, "security": current,
             "manifest_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    print(RUNTIME_PREFIX + payload, flush=True)
    print(KERNEL_PREFIX + json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=APPS)
    args = parser.parse_args(argv)
    try:
        port = int(os.environ.get("PORT") or "8080")
    except ValueError:
        parser.error("PORT must be an integer between 1 and 65535.")
    if not 1 <= port <= 65535:
        parser.error("PORT must be an integer between 1 and 65535.")
    source = os.environ.get("RENDER_GIT_COMMIT", "")
    if not re.fullmatch(r"[a-f0-9]{40}", source):
        raise RuntimeError("Render requires its exact source commit before launch.")
    security = _harden()
    _attest(args.role, source, security)
    os.execv(sys.executable, [
        sys.executable, "-m", "uvicorn", APPS[args.role], "--host", "0.0.0.0",
        "--port", str(port), "--no-access-log",
    ])


if __name__ == "__main__":
    main()
