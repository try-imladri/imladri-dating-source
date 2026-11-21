"""Repair one verified Cloudflare Container source IP from the protected Render standby."""
import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.responses import JSONResponse

PROJECT = "6aefd7b0aa04177eba70c615"
DATABASE = "imladri_personal_live"
PROBE = "https://imladri-personal-candidate.imladri.workers.dev/api/ops/container-egress"
RIPE = "https://stat.ripe.net/data/network-info/data.json"
TOKEN = "https://cloud.mongodb.com/api/oauth/token"
ACCESS = f"https://cloud.mongodb.com/api/atlas/v2/groups/{PROJECT}/accessList"
TAG = "imladri-personal-cf-v1:"
MAX_RULES = 4
LEASE = "container-egress-repair"
log = logging.getLogger(__name__)


class Unverified(Exception):
    """No network change is permitted with incomplete or inconsistent evidence."""


def expect(value):
    if not value:
        raise Unverified()


def owned_tag(address, secret):
    sig = hmac.new(secret.encode(), str(address).encode(), hashlib.sha256).hexdigest()[:24]
    return TAG + sig


def subnet(entry):
    value = entry.get("cidrBlock") or entry.get("ipAddress")
    return ipaddress.ip_network(value, strict=False) if value else None


def owned_expiry(entry, secret):
    net = subnet(entry)
    if not net or net.version != 4 or net.prefixlen != 32:
        return None
    if entry.get("comment") != owned_tag(net.network_address, secret) or not entry.get("deleteAfterDate"):
        return None
    expiry = datetime.fromisoformat(entry["deleteAfterDate"].replace("Z", "+00:00"))
    return expiry if expiry.tzinfo else None


async def observed_address(client, probe_key):
    observation = await client.post(PROBE, headers={"Authorization": "Bearer " + probe_key})
    observation.raise_for_status()
    address = ipaddress.IPv4Address(observation.json()["egress_ipv4"])
    expect(address.is_global and not address.is_multicast and not address.is_reserved)
    response = await client.get(RIPE, params={"resource": str(address)})
    response.raise_for_status()
    route = response.json()["data"]
    expect({str(n) for n in route["asns"]} == {"13335"} and address in ipaddress.ip_network(route["prefix"]))
    return address


async def atlas_headers(client, client_id, secret):
    response = await client.post(TOKEN, auth=(client_id, secret), data={"grant_type": "client_credentials"})
    response.raise_for_status()
    token = response.json()["access_token"]
    expect(isinstance(token, str) and token)
    return {"Authorization": "Bearer " + token, "Accept": "application/vnd.atlas.2024-08-05+json"}


async def current_rules(client, headers):
    response = await client.get(ACCESS, headers=headers, params={"itemsPerPage": 500, "includeCount": "true"})
    response.raise_for_status()
    result = response.json()
    rows = result["results"]
    expect(len(rows) == result["totalCount"] and all(row["groupId"] == PROJECT for row in rows))
    return rows


async def remove_owned(client, headers, record, secret):
    # Atlas identifies deletions by IP. Recheck the exact entry and signed ownership
    # immediately beforehand so an operator's replacement is left alone.
    address = subnet(record).network_address
    url = ACCESS + "/" + str(address)
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    latest = response.json()
    expect(latest.get("groupId") == PROJECT and subnet(latest) == subnet(record)
           and owned_expiry(latest, secret) and latest["deleteAfterDate"] == record["deleteAfterDate"])
    response = await client.delete(url, headers=headers)
    response.raise_for_status()


async def reconcile(client, client_id, secret, probe_key):
    address = await observed_address(client, probe_key)
    headers = await atlas_headers(client, client_id, secret)
    rows = await current_rules(client, headers)
    now = datetime.now(timezone.utc)
    matching = [row for row in rows if (net := subnet(row)) and address in net]
    for row in matching:
        expiry = owned_expiry(row, secret)
        if expiry is None or expiry > now + timedelta(days=1):
            return "covered"
    marked = [row for row in rows if (row.get("comment") or "").startswith(TAG)]
    expect(len(marked) <= MAX_RULES)
    if matching:  # The one verified, expiring entry is due for renewal.
        expect(len(matching) == 1)
        await remove_owned(client, headers, matching[0], secret)
    elif len(marked) == MAX_RULES:
        candidates = [(owned_expiry(row, secret), row) for row in marked]
        owned = [(expiry, row) for expiry, row in candidates if expiry]
        expect(owned)
        await remove_owned(client, headers, min(owned, key=lambda pair: pair[0])[1], secret)
    expiry = now + timedelta(days=7, minutes=-5)
    rule = {"ipAddress": str(address), "comment": owned_tag(address, secret),
            "deleteAfterDate": expiry.strftime("%Y-%m-%dT%H:%M:%SZ")}
    response = await client.post(ACCESS, headers=headers, json=[rule])
    response.raise_for_status()
    rows = await current_rules(client, headers)
    exact = [row for row in rows if row.get("comment") == rule["comment"] and subnet(row) == ipaddress.ip_network(str(address))]
    expect(len(exact) == 1 and abs((owned_expiry(exact[0], secret) - expiry).total_seconds()) < 60)
    return "renewed" if matching else "repaired"


async def repair(db):
    secret = os.environ.get("ATLAS_NETWORK_CLIENT_SECRET")
    client_id = os.environ.get("ATLAS_NETWORK_CLIENT_ID")
    probe_key = os.environ.get("CF_EGRESS_PROBE_SECRET")
    expect(db.name == DATABASE and client_id and secret and probe_key)
    now = datetime.now(timezone.utc)
    ticket = secrets.token_hex(16)
    query = {"_id": LEASE, "$or": [{"retry_after": {"$exists": False}}, {"retry_after": {"$lte": now}}]}
    try:
        await db.ops_state.update_one(query, {"$set": {"ticket": ticket, "retry_after": now + timedelta(minutes=3)}}, upsert=True)
    except DuplicateKeyError:
        return "cooldown"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), follow_redirects=False, trust_env=False) as client:
            return await asyncio.wait_for(reconcile(client, client_id, secret, probe_key), timeout=75)
    finally:
        await db.ops_state.update_one({"_id": LEASE, "ticket": ticket}, {"$unset": {"ticket": ""},
            "$set": {"retry_after": datetime.now(timezone.utc) + timedelta(seconds=75)}})


async def endpoint(request, db):
    headers = {"Cache-Control": "no-store"}
    key = os.environ.get("OPS_CHECK_SECRET", "")
    expected = ("Bearer " + key).encode()
    supplied = request.headers.getlist("authorization")
    if not key or len(supplied) != 1 or not hmac.compare_digest(expected, supplied[0].encode()):
        return JSONResponse({"detail": "Unauthorized."}, status_code=401, headers=headers)
    if request.method != "POST":
        return JSONResponse({"detail": "Method not allowed."}, status_code=405, headers={**headers, "Allow": "POST"})
    if request.query_params or await _has_body(request):
        return JSONResponse({"detail": "No request parameters are accepted."}, status_code=400, headers=headers)
    if getattr(type(db), "backend", None) == "postgresql":
        return JSONResponse({"status": "not_required", "database": "postgresql"}, headers=headers)
    try:
        outcome = await repair(db)
    except (Unverified, httpx.HTTPError, PyMongoError, ValueError, KeyError, TypeError, TimeoutError):
        log.warning("Personal ingress repair could not complete.")
        return JSONResponse({"detail": "Recovery check unavailable."}, status_code=503, headers=headers)
    return JSONResponse({"status": outcome}, status_code=202 if outcome == "cooldown" else 200, headers=headers)


async def _has_body(request):
    async for chunk in request.stream():
        if chunk:
            return True
    return False
