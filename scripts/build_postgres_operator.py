"""Build a reproducible, pure-Python driver ZIP without installing packages."""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


EXPECTED_NAMES = {"pg8000", "scramp", "asn1crypto"}
NATIVE_SUFFIXES = {".so", ".pyd", ".dll", ".dylib", ".exe", ".a", ".o", ".pyc", ".pyo"}


def safe_member(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and bool(path.parts) and not {"..", "__pycache__"}.intersection(path.parts)
            and "\\" not in name and ":" not in name and path.suffix.lower() not in NATIVE_SUFFIXES
            and not any(part.endswith(".data") for part in path.parts))


def build(wheels, requirements, output):
    pins = {}
    for line in Path(requirements).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([a-z0-9]+)==([0-9.]+) --hash=sha256:([a-f0-9]{64})", line.strip())
        if not match or match[1] in pins:
            raise ValueError("Expected three unique exact hash-pinned operator requirements")
        pins[match[1]] = {"version": match[2], "wheel_sha256": match[3]}
    if set(pins) != EXPECTED_NAMES:
        raise ValueError("The operator must contain exactly three reviewed distributions")
    files, distributions = {}, []
    wheel_paths = sorted(Path(wheels).glob("*.whl"))
    if len(wheel_paths) != 3:
        raise ValueError("Expected exactly three pure Python wheels")
    for wheel in wheel_paths:
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        with zipfile.ZipFile(wheel) as archive:
            metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise ValueError("Wheel metadata is missing or ambiguous")
            metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
            name, version = metadata["Name"].lower(), metadata["Version"]
            if name not in pins or pins[name] != {"version": version, "wheel_sha256": digest}:
                raise ValueError("Wheel does not match its reviewed version and hash")
            wheel_metadata = BytesParser().parsebytes(archive.read(metadata_names[0].removesuffix("METADATA") + "WHEEL"))
            tags = wheel_metadata.get_all("Tag", [])
            if wheel_metadata.get("Root-Is-Purelib", "").lower() != "true" or not tags or any(
                    not tag.endswith("-none-any") for tag in tags):
                raise ValueError("Native wheels are forbidden in the operator bundle")
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if not safe_member(member.filename) or stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("Unsafe or compiled content in operator wheel")
                if member.filename in files:
                    raise ValueError("Operator wheels contain duplicate paths")
                files[member.filename] = archive.read(member)
            distributions.append({"name": name, "version": version, "wheel": wheel.name, "wheel_sha256": digest})
    if {item["name"] for item in distributions} != EXPECTED_NAMES:
        raise ValueError("Duplicate or missing operator distribution")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "drivers.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    manifest = {"version": 1, "scope": "explicit Render one-off operator only", "python_distributions": sorted(distributions, key=lambda row: row["name"]),
                "runtime_dependencies": ["python-dateutil==2.9.0.post0", "six==1.17.0"],
                "zip_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in sorted(files.items())],
                "native_or_compiled_files": 0, "reproducible_zip_timestamp": "1980-01-01T00:00:00"}
    (output / "sbom.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    proof = build(args.wheels, args.requirements, args.output)
    print(json.dumps({"operator_distributions": len(proof["python_distributions"]), "zip_sha256": proof["zip_sha256"], "native_or_compiled_files": 0}))
