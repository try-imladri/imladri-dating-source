"""Audit an immutable Wolfi image using its APK database and current vendor data.

The vendor feed includes unresolved investigations and backported fixes. A zero
match count is not a claim that application/native-code reachability was proven.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import requests

if __package__:
    from .audit_release_inventory import osv
else:
    from audit_release_inventory import osv

TOOL_VERSION = "1.1.0"
SELECTION_SCHEMA = 2
VENDOR_ARCHIVE = "https://advisories.cgr.dev/chainguard/v3/osv/chainguard-osv.tar.gz"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
DEFAULT_HELPER = "cgr.dev/chainguard/wolfi-base@sha256:d13acb327aa77acf2752acea8c08456511a5b8142455cc2fe0f966b960bf64c6"
PROBE = r'''
from importlib import metadata
from pathlib import Path
import hashlib,json,os,platform,ssl,sqlite3,sys
database=Path('/usr/lib/apk/db/installed')
raw=database.read_bytes()
packages=[]; owners={}
for block in raw.decode().split('\n\n'):
    fields={}; directory=''
    for line in block.splitlines():
        if len(line)<2 or line[1]!=':': continue
        key,value=line[0],line[2:]
        if key in ('P','V','o','A'): fields[key]=value
        if key=='F': directory=value
        if key=='R' and 'P' in fields:
            owners['/'+directory+'/'+value]=fields['P']
    if fields:
        assert all(key in fields for key in ('P','V','A')), fields
        packages.append({'name':fields['P'],'version':fields['V'],'origin':fields.get('o',fields['P']),'architecture':fields['A']})
assert packages and len({p['name'] for p in packages})==len(packages)
distributions=[]
for item in metadata.distributions():
    evidence=str(item._path)+'/METADATA'
    distributions.append({'name':item.metadata['Name'],'version':item.version,'metadata_path':evidence,'apk_owner':owners.get(evidence)})
status=Path('/proc/self/status').read_text()
caps=next(line.split(':',1)[1].strip() for line in status.splitlines() if line.startswith('CapEff:'))
release=platform.freedesktop_os_release()
assert release['ID'] in ('wolfi','chainguard'), release
pruning_path=Path('/app/backend/runtime_pruning.json')
pruning=None
if pruning_path.exists():
    pruning=json.loads(pruning_path.read_bytes())
    assert pruning['status']=='applied'
    assert pruning['apk_database_sha256_before']==pruning['apk_database_sha256_after']==hashlib.sha256(raw).hexdigest()
    for item in pruning['removed_files']:
        path=Path(item['path'])
        assert path.parent==Path('/usr/lib') and not path.exists() and not path.is_symlink()
    assert not pruning['remaining_removed_library_dynamic_dependencies']
print(json.dumps({'operating_system':release,'architecture':platform.machine(),'python':platform.python_version(),'python_executable':sys.executable,'python_prefix':sys.prefix,'openssl':ssl.OPENSSL_VERSION,'sqlite':sqlite3.sqlite_version,'uid':os.getuid(),'gid':os.getgid(),'effective_capabilities':caps,'apk_database_path':str(database),'apk_database_sha256':hashlib.sha256(raw).hexdigest(),'apk_packages':sorted(packages,key=lambda p:p['name']),'pypi':sorted(distributions,key=lambda p:p['name'].lower()),'runtime_pruning':pruning,'runtime_tools':{path:Path(path).exists() for path in ('/bin/sh','/bin/bash','/bin/busybox','/sbin/apk','/usr/bin/apk','/usr/bin/dpkg','/usr/bin/apt','/usr/bin/gcc','/usr/bin/node')}}))
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def command(arguments, **kwargs):
    kwargs.setdefault("text", True)
    return subprocess.check_output(arguments, **kwargs)


def inventory_image(image, python):
    inspected = json.loads(command(["docker", "image", "inspect", image]))[0]
    image_id = inspected["Id"]
    inventory = json.loads(command(["docker", "run", "--rm", "--network", "none", "--entrypoint", python,
                                    image_id, "-c", PROBE]))
    expected = {"fastapi", "pymongo"}
    discovered = {row["name"].lower().replace("_", "-") for row in inventory["pypi"]}
    if not expected.issubset(discovered):
        raise ValueError("Application Python distributions are missing; audit the interpreter used by the service, including its virtual environment.")
    return {"image_reference": image, "image_id": image_id,
            "repo_digests": inspected.get("RepoDigests", []),
            "image_created": inspected["Created"], "inventory": inventory}


def download_archive(cache_dir):
    """Conditionally refresh the full feed; a failed fetch cannot replace it."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / "chainguard-v3.tar.gz"
    metadata_path = cache_dir / "chainguard-v3.source.json"
    previous = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    cache_valid = (archive.exists() and previous.get("sha256") == sha256_file(archive))
    headers = {"If-None-Match": previous["etag"]} if cache_valid and previous.get("etag") else {}
    response = requests.get(VENDOR_ARCHIVE, headers=headers, stream=True, timeout=(15, 90))
    try:
        if response.status_code == 304:
            if not cache_valid:
                raise ValueError("Server returned unchanged data without a valid cached feed.")
            return archive, {**previous, "checked_utc": now(), "http_status": 304}
        response.raise_for_status()
        temporary = archive.with_name(archive.name + "." + uuid4().hex + ".tmp")
        digest, total = hashlib.sha256(), 0
        try:
            with temporary.open("wb") as stream:
                for block in response.iter_content(1024 * 1024):
                    stream.write(block)
                    digest.update(block)
                    total += len(block)
            expected = response.headers.get("Content-Length")
            if not total or (expected and total != int(expected)):
                raise ValueError("Incomplete vendor archive download.")
            # Parse before replacing the previously verified archive.
            with tarfile.open(temporary, "r|gz") as bundle:
                first = next(iter(bundle), None)
                if first is None:
                    raise ValueError("Empty vendor advisory archive.")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        source = {"url": VENDOR_ARCHIVE, "sha256": digest.hexdigest(), "bytes": total,
                  "downloaded_utc": now(), "checked_utc": now(), "http_status": response.status_code,
                  "last_modified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag")}
        atomic_json(metadata_path, source)
        return archive, source
    finally:
        response.close()


def read_vendor_records(path, names):
    """Stream the archive without unpacking untrusted paths or loading it in RAM."""
    pattern = re.compile(rb'"name"\s*:\s*"(?:' + b"|".join(re.escape(name.encode()) for name in sorted(names)) + rb')"')
    selected, examined, seen = [], 0, set()
    with tarfile.open(path, "r|gz") as bundle:
        for member in bundle:
            # Python 3.12 caches TarInfo objects even in streaming mode.
            bundle.members.clear()
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            if Path(member.name).name == "all.json":
                # The archive also carries its large id/modified index.
                continue
            if member.size > 10 * 1024 * 1024:
                raise ValueError("Unexpected oversized vendor record: " + member.name)
            stream = bundle.extractfile(member)
            raw = stream.read()
            examined += 1
            if not pattern.search(raw):
                continue
            record = json.loads(raw)
            if any(item.get("package", {}).get("name") in names for item in record.get("affected", [])):
                if record["id"] in seen:
                    raise ValueError("Duplicate advisory identity in vendor archive.")
                seen.add(record["id"])
                selected.append(record)
    if not examined:
        raise ValueError("No advisory JSON records in vendor archive.")
    return selected, {"archive_records_examined": examined, "relevant_advisory_records": len(selected)}


def cached_vendor_records(path, names, cache_dir):
    """Reuse a selection only for identical feed bytes and installed package names."""
    identity = {"archive_sha256": sha256_file(path), "package_names": sorted(names),
                "tool_version": TOOL_VERSION, "selection_schema": SELECTION_SCHEMA}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    selected_path = cache_dir / ("selected-" + key + ".json")
    if selected_path.exists():
        cached = json.loads(selected_path.read_text(encoding="utf-8"))
        value = cached.get("value", {})
        checksum = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if value.get("identity") == identity and checksum == cached.get("sha256"):
            return value["records"], {**value["coverage"], "selection_cache_reused": True}
    records, coverage = read_vendor_records(path, names)
    value = {"identity": identity, "records": records, "coverage": coverage}
    atomic_json(selected_path, {"sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(), "value": value})
    return records, {**coverage, "selection_cache_reused": False}


def apk_comparisons(pairs, helper):
    """Use the actual package manager's version ordering, including _git/_rc/-rN."""
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64}", helper):
        raise ValueError("APK comparison helper must be pinned by immutable digest.")
    pairs = sorted(set(pairs))
    if not pairs:
        return {}
    if any(not re.fullmatch(r"[A-Za-z0-9.+_~:-]+", version) for pair in pairs for version in pair):
        raise ValueError("Unexpected APK version format.")
    script = 'while read -r left right; do apk version -t "$left" "$right" || exit 1; done'
    # Binary stdin prevents Windows text-mode LF->CRLF conversion. A trailing
    # carriage return changes APK's comparison of the right-hand version.
    output = command(["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "/bin/sh", helper,
                      "-c", script], input="".join(left + " " + right + "\n" for left, right in pairs).encode(), text=False)
    output = output.decode().splitlines()
    if len(output) != len(pairs) or any(value not in ("<", "=", ">") for value in output):
        raise ValueError("Incomplete or invalid native APK comparison result.")
    return {pair: value for pair, value in zip(pairs, output)}


def relevant_entries(inventory, records):
    packages = {row["name"]: row for row in inventory["inventory"]["apk_packages"]}
    origins = {row["origin"] for row in packages.values()}
    ecosystem = "Wolfi" if inventory["inventory"]["operating_system"]["ID"] == "wolfi" else "Chainguard"
    for record in records:
        if record.get("withdrawn"):
            continue
        for affected in record.get("affected", []):
            package = affected.get("package", {})
            name = package.get("name")
            if package.get("ecosystem") != ecosystem:
                continue
            architecture = parse_qs(urlparse(package.get("purl", "")).query).get("arch", [None])[0]
            if name not in packages:
                if name in origins:
                    installed_architectures = {row["architecture"] for row in packages.values() if row["origin"] == name}
                    if architecture and architecture not in installed_architectures | {"noarch"}:
                        continue
                    inherits = affected.get("database_specific", {}).get("chainguard", {}).get("applies_to_subpackages")
                    if inherits is not False:
                        raise ValueError("Origin-level advisory inheritance requires explicit review: " + name + "/" + record["id"])
                continue
            installed = packages[name]
            if architecture and architecture not in (installed["architecture"], "noarch"):
                continue
            yield record, affected, installed, architecture


def comparison_pairs(inventory, records):
    result = set()
    for _, affected, installed, _ in relevant_entries(inventory, records):
        for interval in affected.get("ranges", []):
            for event in interval.get("events", []):
                for version in event.values():
                    if version != "0":
                        result.add((installed["version"], version))
    return result


def range_status(version, ranges, compare):
    if not ranges:
        return "under_investigation", []
    any_affected, had_range, fixed_versions = False, False, []
    only_false_positive = True
    for interval in ranges:
        if interval.get("type") != "ECOSYSTEM":
            raise ValueError("Unexpected non-APK advisory range type.")
        active = False
        events = interval.get("events", [])
        if not events:
            raise ValueError("Empty APK advisory range.")
        had_range = True
        for event in events:
            if len(event) != 1:
                raise ValueError("Ambiguous APK range event.")
            key, boundary = next(iter(event.items()))
            ordering = "=" if boundary == version else (">" if boundary == "0" else compare[(version, boundary)])
            if key == "introduced":
                if ordering in ("=", ">"):
                    active = True
            elif key == "fixed":
                fixed_versions.append(boundary)
                if boundary != "0":
                    only_false_positive = False
                if ordering in ("=", ">"):
                    active = False
            elif key == "last_affected":
                only_false_positive = False
                if ordering == ">":
                    active = False
            elif key == "limit":
                only_false_positive = False
                if ordering in ("=", ">"):
                    active = False
            else:
                raise ValueError("Unsupported APK range event: " + key)
        any_affected |= active
        if not any(event.get("fixed") == "0" for event in events):
            only_false_positive = False
    if any_affected:
        return "affected", sorted(set(fixed_versions) - {"0"})
    if had_range and only_false_positive:
        return "not_affected", []
    return "fixed_or_outside_affected_range", sorted(set(fixed_versions) - {"0"})


def evaluate(inventory, records, compare, exploited, old_ledger):
    rows, seen = [], set()
    for record, affected, installed, architecture in relevant_entries(inventory, records):
        identity = (record["id"], installed["name"], architecture)
        if identity in seen:
            raise ValueError("Duplicate package/architecture advisory entry.")
        seen.add(identity)
        status, fixes = range_status(installed["version"], affected.get("ranges", []), compare)
        components = affected.get("ecosystem_specific", {}).get("components", [])
        states = sorted({item.get("latest_event_status", "unknown") for item in components})
        if affected.get("versions"):
            raise ValueError("Explicit APK affected-version lists require review before using this scanner.")
        if not architecture:
            status = "under_investigation"
        elif status == "affected" and (not states or "detection" in states or "unknown" in states):
            status = "under_investigation"
        elif status in ("not_affected", "fixed_or_outside_affected_range") and (not states or any(
                value not in ("fixed", "false_positive_determination") for value in states)):
            status = "under_investigation"
        aliases = sorted(set(record.get("upstream") or []) | set(record.get("aliases") or []))
        cves = [value for value in aliases if value.startswith("CVE-")]
        available_fixes = [fix for fix in fixes if compare.get((installed["version"], fix)) == "<"]
        rows.append({"advisory": record["id"], "modified": record.get("modified"),
                     "advisory_sha256": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest(),
                     "name": installed["name"], "origin": installed["origin"], "version": installed["version"],
                     "architecture": architecture, "aliases": aliases, "cves": cves,
                     "status": status, "vendor_component_states": states, "vendor_components": components,
                     "ranges": affected.get("ranges", []), "fixed_versions": fixes,
                     "unapplied_vendor_fix_versions": available_fixes,
                     "fix_repository_availability": "not_checked",
                     "known_exploited": [cve for cve in cves if cve in exploited],
                     "severity": record.get("severity"), "references": record.get("references"),
                     "reference": "https://advisories.cgr.dev/chainguard/v3/osv/" + record["id"] + ".json"})
    open_rows = [row for row in rows if row["status"] in ("affected", "under_investigation")]
    alias_rows = {}
    for row in rows:
        for cve in row["cves"]:
            alias_rows.setdefault(cve, []).append(row)
    retained = []
    installed_origins = {row["origin"] for row in inventory["inventory"]["apk_packages"]}
    same_family = {"sqlite3": "sqlite", "gcc-14": "gcc", "glibc": "glibc", "bzip2": "bzip2"}
    for previous in old_ledger.get("records", []):
        matching = alias_rows.get(previous["cve"], [])
        source = previous["source"]
        family = same_family.get(source, source)
        family_installed = any(origin == family or re.fullmatch(re.escape(family) + r"-\d+(?:\.\d+)*", origin)
                               for origin in installed_origins)
        retained.append({"debian_source": source, "cve": previous["cve"], "previous_decision": previous["decision"],
                         "wolfi_advisories": [{"advisory": row["advisory"], "name": row["name"], "status": row["status"]} for row in matching],
                         "comparison": "vendor_records_available" if matching else ("shared_source_family_needs_review" if family_installed else "debian_source_not_installed"),
                         "note": "Source-family mapping is conservative metadata comparison; it does not prove native-code absence or reachability."})
    return {"raw_open_package_advisory_matches": len(open_rows),
            "open_unique_cves": sorted({cve for row in open_rows for cve in row["cves"]}),
            "record_statuses": dict(Counter(row["status"] for row in rows)),
            "known_exploited_open": sorted({cve for row in open_rows for cve in row["known_exploited"]}),
            "records": rows, "previous_debian_records": retained}


def fetch_kev():
    response = requests.get(KEV, timeout=60)
    response.raise_for_status()
    value = response.json()
    return {item["cveID"] for item in value["vulnerabilities"]}, {
        "url": KEV, "checked_utc": now(), "sha256": hashlib.sha256(response.content).hexdigest(),
        "catalog_version": value.get("catalogVersion"), "date_released": value.get("dateReleased")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--python", default="python", help="Application interpreter resolved using the image's PATH, including its virtualenv.")
    parser.add_argument("--apk-helper", default=DEFAULT_HELPER)
    parser.add_argument("--cache-dir", type=Path, default=Path(".local/wolfi-audit"))
    parser.add_argument("--debian-ledger", type=Path, default=Path(__file__).resolve().parents[1] / "security/debian_findings.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = inventory_image(args.image, args.python)
    old_ledger = json.loads(args.debian_ledger.read_text(encoding="utf-8"))
    packages = inventory["inventory"]["apk_packages"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        archive_result = pool.submit(download_archive, args.cache_dir)
        kev_result = pool.submit(fetch_kev)
        # APK-owned distributions are assessed with vendor backport metadata.
        python_packages = sorted({(row["name"], row["version"]) for row in inventory["inventory"]["pypi"] if not row["apk_owner"]})
        python_result = pool.submit(osv, "PyPI", python_packages)
        archive, source = archive_result.result()
        selection_names = {row["name"] for row in packages} | {row["origin"] for row in packages}
        records, coverage = cached_vendor_records(archive, selection_names, args.cache_dir)
        exploited, kev_source = kev_result.result()
        python_matches = python_result.result()
    compare = apk_comparisons(comparison_pairs(inventory, records), args.apk_helper)
    result = evaluate(inventory, records, compare, exploited, old_ledger)
    pypi_source = {"url": "https://api.osv.dev/v1/querybatch", "checked_utc": now(),
                   "query_packages_sha256": hashlib.sha256(json.dumps(python_packages, sort_keys=True).encode()).hexdigest(),
                   "matched_results_sha256": hashlib.sha256(json.dumps(python_matches, sort_keys=True).encode()).hexdigest(),
                   "note": "Query and parsed-match fingerprints, not a claim of an immutable upstream OSV snapshot."}
    report = {"tool": {"name": "audit_wolfi_security", "version": TOOL_VERSION,
                       "source_sha256": sha256_file(Path(__file__)), "apk_version_helper": args.apk_helper},
              "checked_utc": now(), **inventory, "sources": {"vendor": source, "cisa": kev_source, "pypi_osv": pypi_source},
              "coverage": {**coverage, "installed_apk_packages": len(packages),
                           "python_distributions": len(inventory["inventory"]["pypi"]),
                           "pypi_osv_queried": len(python_packages), "pypi_osv_checked_utc": now()},
              "scope": "Exact local immutable image. Provider deployment verification, bundled-wheel native components, input reachability, and repository fix availability require separate evidence.",
              "pypi_osv_matches": python_matches, **result}
    atomic_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("image_id", "raw_open_package_advisory_matches", "record_statuses", "known_exploited_open")}))


if __name__ == "__main__":
    main()
