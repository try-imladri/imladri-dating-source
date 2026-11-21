"""imaldri extra API tests: memories edit/delete-derived, exchange delete, export/delete-me, upgrade, report refresh, admin RBAC, media."""
import json
import os
import subprocess

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"
QA = "t1qa+clerk_test@example.com"  # dedicated throwaway test user
MEMBER = "tester+clerk_test@example.com"


def _sess(email):
    tok = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))["token"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def qa():
    return _sess(QA)


# public media + archetype detail
def test_public_media_jpeg():
    figs = requests.get(f"{API}/public/figures").json()
    assert figs
    for f in figs:
        assert all(m.get("name") for m in f["matches"]), f["slug"]
    lb = requests.get(f"{API}/public/archetypes/love-bomber").json()
    blob = json.dumps(lb)
    import re
    ids = set(re.findall(r"/api/media/([A-Za-z0-9_-]+)", blob))
    assert ids, "no media refs in love-bomber"
    for mid in list(ids)[:4]:
        r = requests.get(f"{API}/media/{mid}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg"), mid


def test_admin_forbidden_more():
    s = _sess(MEMBER)
    assert s.get(f"{API}/admin/figures").status_code == 403
    assert s.put(f"{API}/admin/settings", json={"people_limit": 99}).status_code == 403
    assert s.post(f"{API}/admin/waves/1", json={"unlocked": False}).status_code == 403
    assert s.put(f"{API}/admin/archetypes/love-bomber", json={"unlocked": False}).status_code == 403
    assert requests.get(f"{API}/people", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_upgrade_and_schema(qa):
    u = qa.get(f"{API}/upgrade")
    assert u.status_code == 200 and u.json()["tier"] == "free"
    sch = requests.get(f"{API}/intake-schema").json()
    assert len(sch) == 4


def test_memory_flow_and_delete_me(qa):
    qa.delete(f"{API}/me")
    r = qa.post(f"{API}/people", json={"nickname": "TEST_QA", "rel_type": "crush", "stage": "crush", "background": "met at gym"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    # validation
    assert qa.post(f"{API}/people", json={"nickname": "", "rel_type": "crush", "stage": "crush", "background": "x"}).status_code == 422
    assert qa.post(f"{API}/people/{pid}/exchanges", json={}).status_code == 422
    r = qa.post(f"{API}/people/{pid}/exchanges", timeout=150, json={
        "conversation": [{"speaker": "them", "text": "hey stranger"}, {"speaker": "me", "text": "hi! long time"},
                         {"speaker": "them", "text": "we should get drinks sometime"}], "question": "Is he interested?"})
    assert r.status_code == 200, r.text
    ex = r.json()
    r = qa.post(f"{API}/exchanges/{ex['id']}/memories", json={"memories": [
        {"kind": "event", "text": "TEST he suggested drinks"}, {"kind": "interpretation", "text": "TEST he is interested"}]})
    assert r.json()["saved"] == 2
    mems = [m for m in qa.get(f"{API}/people/{pid}/memories").json() if m["kind"] != "profile_detail"]
    ev = next(m for m in mems if m["kind"] == "event")
    interp = next(m for m in mems if m["kind"] == "interpretation")
    assert ev["id"] in interp["derived_from"]
    assert qa.patch(f"{API}/memories/{ev['id']}", json={"text": "TEST edited event"}).status_code == 200
    r = qa.delete(f"{API}/memories/{ev['id']}")
    assert r.status_code == 200 and r.json()["derived_removed"] == 1
    ids = [m["id"] for m in qa.get(f"{API}/people/{pid}/memories").json()]
    assert ev["id"] not in ids and interp["id"] not in ids
    # profile detail delete
    assert qa.delete(f"{API}/people/{pid}/details/background").status_code == 200
    # patterns list
    assert qa.get(f"{API}/patterns").status_code == 200
    # export
    exp = qa.get(f"{API}/me/export").json()
    assert len(exp["people"]) == 1 and len(exp["exchanges"]) == 1
    # delete exchange
    assert qa.delete(f"{API}/exchanges/{ex['id']}").status_code == 200
    assert qa.get(f"{API}/people/{pid}/exchanges").json() == []
    # delete everything
    assert qa.delete(f"{API}/me").status_code == 200
    assert qa.get(f"{API}/people").json()["people"] == []
    me = qa.get(f"{API}/me").json()
    assert me["onboarded"] is False and me["onboarding_step"] == 1
