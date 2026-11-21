"""Remove unused AVIF wheel binaries from the JPEG/PNG-only dating runtime.

The app never accepts AVIF. Its optional decoder brings a C++ runtime dependency
that the existing native-runtime policy deliberately excludes. Keep that policy
and the general ELF dependency check intact, and record the exact removed bytes.
"""
import argparse
import hashlib
import json
from pathlib import Path


def prune(venv, manifest):
    venv = Path(venv).resolve()
    if venv.name != "venv" or not (venv / "pyvenv.cfg").is_file():
        raise ValueError("An explicit build virtual environment is required.")
    records = []
    for site in sorted(venv.glob("lib/python*/site-packages")):
        for pattern in ("PIL/_avif*.so", "pillow.libs/libavif*.so*"):
            for path in sorted(site.glob(pattern)):
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(venv) or not path.is_file() or path.is_symlink():
                    raise ValueError("Unexpected Pillow codec path.")
                records.append({"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"reason":"Optional AVIF codec is outside the dating JPEG/PNG allowlist."})
                path.unlink()
    target = Path(manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema_version":1,"removed_files":records,"scope":"Pillow AVIF binaries only; JPEG/PNG and general ELF checks retained."},indent=2) + "\n",encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv",required=True)
    parser.add_argument("--manifest",required=True)
    args = parser.parse_args()
    prune(args.venv,args.manifest)
