"""Remove explicitly unused native libraries from a staged, audited runtime root.

The APK database is retained byte-for-byte. This records component removal,
not a changed package version or a general static-code clearance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


FAMILIES = {
    "libstdc++": {"pattern": "libstdc++.so*", "owners": {"libstdc++"},
                 "reason": "Unused GNU C++ shared runtime; retained native extensions are reviewed separately."},
    "libmpdec++": {"pattern": "libmpdec++.so*", "owners": {"mpdecimal"},
                  "reason": "Unused C++ Decimal wrapper; Python Decimal retains the C libmpdec implementation."},
    "libform": {"pattern": "libform.so*", "owners": {"ncurses"},
                "reason": "Unused ncurses form validation library; required curses and panel libraries remain."},
    "libformw": {"pattern": "libformw.so*", "owners": {"ncurses"},
                 "reason": "Unused wide ncurses form validation library; required curses and panel libraries remain."},
}
TARGETS = tuple(family["pattern"] for family in FAMILIES.values())
REMOVED_DEPENDENCY_PREFIXES = ("libstdc++", "libmpdec++", "libform.so", "libformw.so")


def elf_needed(path):
    """Read ELF64 little-endian DT_NEEDED entries without running the binary."""
    raw = path.read_bytes()
    if len(raw) < 4 or raw[:4] != b"\x7fELF":
        return None
    if len(raw) < 64 or raw[4:6] != b"\x02\x01":
        raise ValueError("Unsupported or incomplete ELF format: " + str(path))
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", raw)
    phoff, phentsize, phnum = header[5], header[9], header[10]
    if phentsize < 56 or phoff + phentsize * phnum > len(raw):
        raise ValueError("Invalid ELF program headers: " + str(path))
    segments = [struct.unpack_from("<IIQQQQQQ", raw, phoff + index * phentsize)
                for index in range(phnum)]
    dynamic = [segment for segment in segments if segment[0] == 2]
    if not dynamic:
        return []
    if len(dynamic) != 1:
        raise ValueError("Ambiguous ELF dynamic segment: " + str(path))
    offset, length = dynamic[0][2], dynamic[0][5]
    if length % 16 or offset + length > len(raw):
        raise ValueError("Invalid ELF dynamic bounds: " + str(path))
    entries = []
    for index in range(offset, offset + length, 16):
        tag, value = struct.unpack_from("<qQ", raw, index)
        if tag == 0:
            break
        entries.append((tag, value))
    wanted = [value for tag, value in entries if tag == 1]
    if not wanted:
        return []
    tables = [value for tag, value in entries if tag == 5]
    sizes = [value for tag, value in entries if tag == 10]
    if len(tables) != 1 or len(sizes) != 1:
        raise ValueError("Missing ELF string table: " + str(path))
    table = tables[0]
    mappings = [segment for segment in segments
                if segment[0] == 1 and segment[3] <= table < segment[3] + segment[5]]
    if len(mappings) != 1:
        raise ValueError("Unmapped ELF string table: " + str(path))
    table_offset = mappings[0][2] + table - mappings[0][3]
    if table_offset + sizes[0] > len(raw):
        raise ValueError("Invalid ELF string-table bounds: " + str(path))
    result = []
    for value in wanted:
        if value >= sizes[0]:
            raise ValueError("Invalid ELF dependency offset: " + str(path))
        start = table_offset + value
        end = raw.find(b"\0", start, table_offset + sizes[0])
        if end == -1:
            raise ValueError("Unterminated ELF dependency: " + str(path))
        result.append(raw[start:end].decode("ascii"))
    return result


def package_owners(database):
    result = {}
    for block in database.decode().split("\n\n"):
        fields, directory = {}, ""
        for line in block.splitlines():
            if len(line) < 2 or line[1] != ":":
                continue
            tag, value = line[0], line[2:]
            if tag in ("P", "V", "o"):
                fields[tag] = value
            elif tag == "F":
                directory = value
            elif tag == "R" and "P" in fields:
                result["/" + directory + "/" + value] = fields.copy()
    return result


def stage_root(root):
    root = root.resolve()
    if root == Path("/") or root.name != "runtime":
        raise ValueError("Component pruning requires an explicit staged directory named runtime.")
    database = root / "usr/lib/apk/db/installed"
    if not database.is_file():
        raise ValueError("The staged runtime APK database must be retained.")
    return root, database


def scan_sources(root, venv):
    sources = [(root / "usr", root), (venv, venv.parent)]
    seen, rows = set(), []
    for directory, base in sources:
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.stat().st_size < 4:
                continue
            real = path.resolve()
            if real in seen:
                continue
            seen.add(real)
            dependencies = elf_needed(path)
            if dependencies is None:
                continue
            location = ("/" + path.relative_to(root).as_posix()
                        if path.is_relative_to(root) else "/opt/" + path.relative_to(venv.parent).as_posix())
            rows.append({"path": location, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "needed": dependencies})
    return rows


def prune(root, venv, manifest, apply=False):
    root, database = stage_root(Path(root))
    venv = Path(venv).resolve()
    if not venv.is_dir():
        raise ValueError("The application virtual environment must be scanned.")
    manifest = Path(manifest).resolve()
    if not manifest.is_relative_to(root) or manifest == database:
        raise ValueError("The pruning manifest must be written inside the staged runtime.")
    raw_database = database.read_bytes()
    owners = package_owners(raw_database)
    candidates = sorted({path for pattern in TARGETS for path in (root / "usr/lib").glob(pattern)})
    removals = []
    for path in candidates:
        family_name = next(name for name, family in FAMILIES.items() if path.match(family["pattern"]))
        family = FAMILIES[family_name]
        if not (path.is_symlink() or path.is_file()):
            raise ValueError("Unexpected native component type: " + str(path))
        real = path.resolve(strict=True)
        if not real.is_relative_to(root):
            raise ValueError("Native component resolves outside the staged runtime.")
        location = "/" + path.relative_to(root).as_posix()
        owner = owners.get(location)
        if not owner or owner["P"] not in family["owners"]:
            raise ValueError("Unexpected native package ownership: " + location)
        removals.append({"path": location, "kind": "symlink" if path.is_symlink() else "file",
                         "link_target": str(path.readlink()) if path.is_symlink() else None,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "owner": owner, "family": family_name, "reason": family["reason"]})
    rows = scan_sources(root, venv)
    removed_locations = {row["path"] for row in removals}
    remaining = [row for row in rows if row["path"] not in removed_locations]
    callers = [row for row in remaining
               if any(name.startswith(REMOVED_DEPENDENCY_PREFIXES) for name in row["needed"])]
    if callers:
        raise ValueError("Remaining ELF files require removed native libraries: " + ", ".join(row["path"] for row in callers))
    # Finish every validation before deleting anything. Keep metadata and C Decimal.
    if not list((root / "usr/lib").glob("libmpdec.so.*")):
        raise ValueError("The C Decimal library must remain available.")
    if apply:
        for path in candidates:
            path.unlink()
        if database.read_bytes() != raw_database:
            raise ValueError("The APK database changed during component pruning.")
        if any(list((root / "usr/lib").glob(pattern)) for pattern in TARGETS):
            raise ValueError("Native component removal did not complete.")
    evidence = {"schema_version": 1, "status": "applied" if apply else "reviewed_not_applied",
                "scope": "Only explicitly listed unused C++ and ncurses form-library files; package metadata retained.",
                "apk_database_sha256_before": hashlib.sha256(raw_database).hexdigest(),
                "apk_database_sha256_after": hashlib.sha256(database.read_bytes()).hexdigest(),
                "removed_files": removals, "remaining_elf_count": len(remaining),
                "remaining_elf_dependencies": remaining,
                "remaining_removed_library_dynamic_dependencies": callers,
                "preserved_components": "C libmpdec plus ncurses/curses and panel functionality",
                "limit": "DT_NEEDED validation is not by itself a clearance of statically embedded C++ code; native source/build provenance is reviewed separately."}
    if apply:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf8")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = prune(args.root, args.venv, args.manifest, args.apply)
    print(json.dumps({"status": result["status"], "removed_files": len(result["removed_files"]),
                      "remaining_elf_count": result["remaining_elf_count"]}))


if __name__ == "__main__":
    main()
