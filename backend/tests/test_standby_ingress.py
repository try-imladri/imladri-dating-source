"""Mock provider contracts and exercise the standby's Mongo-backed repair lease on loopback."""
import asyncio
import ipaddress
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from backend import standby_ingress as ingress


FIXTURE_SECRET = "fixture-network-only"
ADDRESS = "104.16.10.10"


def row(ip, *, expires=4, comment=None):
    return {"groupId": ingress.PROJECT, "ipAddress": ip, "cidrBlock": f"{ip}/32",
            "comment": comment or ingress.owned_tag(ip, FIXTURE_SECRET),
            "deleteAfterDate": (datetime.now(timezone.utc) + timedelta(days=expires)).strftime("%Y-%m-%dT%H:%M:%SZ")}


class Providers:
    def __init__(self, rows=(), address=ADDRESS, asns=("13335",)):
        self.rows = {r["ipAddress"]: r.copy() for r in rows}
        self.address = address
        self.asns = asns
        self.mutations = []
        self.requests = []
        self.replace_on_read = False

    def __call__(self, request):
        url = str(request.url).split("?")[0]
        self.requests.append((request.method, url))
        if url == ingress.PROBE:
            assert request.method == "POST" and request.headers["Authorization"] == "Bearer fixture-probe" and not request.content
            return httpx.Response(200, json={"egress_ipv4": self.address})
        if url == ingress.RIPE:
            assert "Authorization" not in request.headers
            return httpx.Response(200, json={"data": {"asns": self.asns, "prefix": "104.16.0.0/13"}})
        if url == ingress.TOKEN:
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token":"fixture-atlas-token"})
        assert request.headers["Authorization"] == "Bearer fixture-atlas-token"
        if url == ingress.ACCESS and request.method == "GET":
            return httpx.Response(200, json={"results": list(self.rows.values()), "totalCount": len(self.rows)})
        if url == ingress.ACCESS and request.method == "POST":
            new, = json.loads(request.content)
            addr = new["ipAddress"]
            assert ipaddress.ip_network(addr).prefixlen == 32
            assert addr not in self.rows and not any(x in new for x in ("cidrBlock","awsSecurityGroup"))
            self.rows[addr] = {**new,"groupId":ingress.PROJECT,"cidrBlock":addr+"/32"}
            self.mutations.append(("add",addr))
            return httpx.Response(201,json={})
        addr = url.split("/")[-1]
        if request.method == "GET":
            result = self.rows[addr].copy()
            if self.replace_on_read:
                result["comment"] = "operator-owned"
            return httpx.Response(200, json=result)
        assert request.method == "DELETE"
        self.mutations.append(("remove",addr))
        del self.rows[addr]
        return httpx.Response(204)

    async def reconcile(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self)) as client:
            return await ingress.reconcile(client,"fixture-client",FIXTURE_SECRET,"fixture-probe")


def test_repair_is_exact_idempotent_and_guards_existing_rules():
    api = Providers()
    assert asyncio.run(api.reconcile()) == "repaired"
    assert asyncio.run(api.reconcile()) == "covered"
    assert api.mutations == [("add", ADDRESS)]
    lifetime = ingress.owned_expiry(api.rows[ADDRESS], FIXTURE_SECRET) - datetime.now(timezone.utc)
    assert timedelta(days=6) < lifetime < timedelta(days=7)
    api.rows[ADDRESS] = row(ADDRESS, comment="user's existing entry", expires=0.1)
    assert asyncio.run(api.reconcile()) == "covered" and len(api.mutations) == 1


@pytest.mark.parametrize("address,asns", [("127.0.0.1",["13335"]),("224.0.0.1",["13335"]),
                                         ("104.16.10.11/24",["13335"]), (ADDRESS, ["13335","64500"]),
                                         ("8.8.8.8",["13335"])])
def test_rejects_unverified_network_or_non_ip(address,asns):
    api = Providers(address=address,asns=asns)
    with pytest.raises((ingress.Unverified,ValueError)):
        asyncio.run(api.reconcile())
    assert not api.mutations and ("POST",ingress.TOKEN) not in api.requests


def test_rotation_renewal_and_operator_replacement():
    ips = ["104.16.1."+str(n) for n in range(1,5)]
    api = Providers([row(ip, expires=n+1) for n, ip in enumerate(ips)])
    assert asyncio.run(api.reconcile()) == "repaired"
    assert api.mutations == [("remove",ips[0]),("add",ADDRESS)] and len(api.rows) == 4
    api = Providers([row(ADDRESS,expires=0.2)])
    api.replace_on_read = True
    with pytest.raises(ingress.Unverified): asyncio.run(api.reconcile())
    assert not api.mutations
    api.replace_on_read = False
    assert asyncio.run(api.reconcile()) == "renewed"
    assert api.mutations == [("remove",ADDRESS),("add",ADDRESS)]


def test_invalid_owned_markers_cannot_be_evicted():
    rows = [row("104.16.1."+str(i), comment=ingress.TAG+"stale-secret") for i in range(4)]
    api = Providers(rows)
    with pytest.raises(ingress.Unverified): asyncio.run(api.reconcile())
    assert not api.mutations


def test_standby_auth_input_validation_and_cross_request_lease(monkeypatch):
    uri = os.getenv("IMLADRI_BILLING_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_BILLING_TEST_MONGO for local MongoDB")
    assert urlsplit(uri).hostname in {"127.0.0.1","localhost","::1"}
    from backend import standby
    for name, value in {"RENDER_ORIGIN_SECRET":"fixture-origin","OPS_CHECK_SECRET":"fixture-ops",
                        "CF_EGRESS_PROBE_SECRET":"fixture-probe","ATLAS_NETWORK_CLIENT_SECRET":FIXTURE_SECRET,
                        "ATLAS_NETWORK_CLIENT_ID":"fixture-client"}.items():
        monkeypatch.setenv(name,value)
    calls = []
    async def reconcile(*args):
        calls.append(1)
        await asyncio.sleep(0.05)
        return "repaired"
    monkeypatch.setattr(ingress,"reconcile",reconcile)

    async def run():
        conn=AsyncIOMotorClient(uri)
        db=conn["network_lease_test_"+uuid.uuid4().hex]
        monkeypatch.setattr(standby,"db",SimpleNamespace(name=ingress.DATABASE,ops_state=db.ops_state))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=standby.app),base_url="http://localhost") as http:
                path = "/api/ops/repair-ingress"
                origin = {"X-Imladri-Origin":"fixture-origin"}
                headers = {**origin,"Authorization":"Bearer fixture-ops"}
                assert (await http.post(path)).status_code == 404
                assert (await http.post(path,headers=origin)).status_code == 401
                assert (await http.post(path+"?ip=1.1.1.1",headers=headers)).status_code == 400
                assert (await http.post(path,headers=headers,content=b"104.16.0.1")).status_code == 400
                assert (await http.get(path,headers=headers)).status_code == 405
                responses = await asyncio.gather(*(http.post(path,headers=headers) for _ in range(10)))
                assert sorted(r.status_code for r in responses)==[200]+[202]*9 and len(calls)==1
        finally:
            await conn.drop_database(db.name)
            conn.close()
    asyncio.run(run())
