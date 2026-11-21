"""Refresh a scoped Debian exposure ledger against the public vendor and OSV feeds."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import requests

if __package__:
    from .audit_release_inventory import osv
else:
    from audit_release_inventory import osv

TRACKER = "https://security-tracker.debian.org/tracker/data/json"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def vendor_fingerprint(entry, release):
    return fingerprint({"description": entry.get("description"), "scope": entry.get("scope"),
                        "release": entry.get("releases", {}).get(release)})


def evidence_files_current(ledger):
    root = Path(__file__).resolve().parents[1]
    proofs = ledger.get("proof_files", [])
    for proof in proofs:
        try:
            observed = json.loads((root / proof["path"]).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        if fingerprint(observed) != proof["canonical_json_sha256"]:
            return False
    return bool(proofs)


def review(inventory, ledger, matches, tracker, exploited, evidence_current=True):
    system = inventory["inventory"]["operating_system"]
    if system["id"] != "debian":
        raise ValueError("The inventory must describe a Debian image.")
    release = system.get("codename")
    if not release:
        if system["version"] != ledger["artifact"]["operating_system"]["version"]:
            raise ValueError("Refresh the inventory with its release codename before reviewing a different Debian release.")
        release = ledger["artifact"]["operating_system"]["codename"]
    known = {(row["source"], row["cve"]): row for row in ledger["records"]}
    versions = dict(inventory["inventory"]["debian_sources"])
    current_image = inventory["image_id"]
    current_keys = set()
    current_matches = set()
    rows = []
    for match in matches:
        source = match["name"]
        cve = match["advisory"].removeprefix("DEBIAN-")
        key = (source, cve)
        identity = (source, match["version"], cve)
        if identity in current_matches:
            raise ValueError("Duplicate source/advisory match in the feed result.")
        current_matches.add(identity)
        current_keys.add(key)
        previous = known.get(key)
        entry = tracker.get(source, {}).get(cve, {})
        vendor = entry.get("releases", {}).get(release, {})
        verified = bool(evidence_current and previous
                        and current_image == ledger["artifact"]["image_id"]
                        and previous["version"] == match["version"]
                        and previous["vendor_record_sha256"] == vendor_fingerprint(entry, release))
        decision = previous["decision"] if verified else "needs_review"
        action = previous["next_action"] if verified else "Recheck affected components and input paths on the supplied image."
        if vendor.get("fixed_version") == "0":
            decision = "not_affected"
            action = "Retain the vendor's not-affected evidence and recheck when the vendor record or Debian release changes."
            verified = True
        elif vendor.get("status") == "resolved" and vendor.get("fixed_version"):
            decision = "verify_vendor_fix"
            action = "Compare the installed version with the vendor fix and signed stable candidate, then test the updated image."
        rows.append({
            "source": source, "version": match["version"], "cve": cve,
            "vendor_status": vendor.get("status", "unavailable"),
            "vendor_fixed_version": vendor.get("fixed_version"),
            "decision": decision, "review_proof_current": verified,
            "known_exploited": cve in exploited, "next_action": action,
            "owner": previous["owner"] if previous else "Imladri security maintenance",
            "reference": "https://security-tracker.debian.org/tracker/" + cve,
        })
    absent = []
    for key, previous in known.items():
        if key not in current_keys:
            absent.append({
                "source": key[0], "cve": key[1],
                "status": "not_currently_reported",
                "source_installed": key[0] in versions,
                "action": "Retain the history and verify why the record disappeared before recording a fixed-package closure.",
                "previous_decision": previous["decision"],
            })
    return {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "image_id": current_image,
        "scope": "The supplied immutable image inventory. Active provider deployments are verified separately.",
        "ecosystem": "Debian:" + system["version"].split(".")[0],
        "release": release, "raw_matches": len(rows),
        "supporting_proof_files_valid": evidence_current,
        "decisions": dict(Counter(row["decision"] for row in rows)),
        "vendor_states": dict(Counter(row["vendor_status"] for row in rows)),
        "proofs_requiring_review": sum(not row["review_proof_current"] for row in rows),
        "known_exploited": [row["cve"] for row in rows if row["known_exploited"]],
        "records": rows, "no_longer_reported": absent,
    }


def fetch(url):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, default=Path(__file__).resolve().parents[1] / "security/debian_findings.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    ecosystem = "Debian:" + inventory["inventory"]["operating_system"]["version"].split(".")[0]
    with ThreadPoolExecutor(max_workers=3) as pool:
        vendor_result = pool.submit(fetch, TRACKER)
        exploited_result = pool.submit(fetch, KEV)
        match_result = pool.submit(osv, ecosystem, inventory["inventory"]["debian_sources"])
        tracker = vendor_result.result()
        exploited = {row["cveID"] for row in exploited_result.result()["vulnerabilities"]}
        matches = match_result.result()
    report = review(inventory, ledger, matches, tracker, exploited, evidence_files_current(ledger))
    temporary = args.output.with_name(args.output.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({key: report[key] for key in (
        "image_id", "raw_matches", "decisions", "proofs_requiring_review", "known_exploited")}))


if __name__ == "__main__":
    main()
