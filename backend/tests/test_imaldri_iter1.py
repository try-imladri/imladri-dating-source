"""Iteration-1 extra regression: derived memory deletion, archetype tag rules, export, waitlist toggle, exchange delete cascade."""
import json
import subprocess

import pytest
import requests

BASE_URL = ""
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"
QA = "tester3+clerk_test@example.com"
ADMIN = "tester+admin_clerk_test@example.com"


def _sess(email):
    tok = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))["token"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def qa():
    s = _sess(QA)
    ids = []
    s.ids = ids
    yield s
    for pid in ids:
        s.delete(f"{API}/people/{pid}")


def _person(qa, rel="crush", name="TEST_Iter"):
    r = qa.post(f"{API}/people", json={"nickname": name, "rel_type": rel, "stage": "early", "background": "met at a party"})
    assert r.status_code == 200, r.text
    qa.ids.append(r.json()["id"])
    return r.json()


# memories: derived deletion + exchange delete cascade
def test_derived_deletion_and_exchange_cascade(qa):
    p = _person(qa)
    r = qa.post(f"{API}/people/{p['id']}/exchanges", timeout=150, json={
        "conversation": [{"speaker": "them", "text": "hey"}, {"speaker": "me", "text": "hi"}], "question": "Is he into me?"})
    assert r.status_code == 200, r.text
    ex = r.json()
    for k in ("what_they_are_doing", "what_it_means", "what_changed", "best_move", "what_to_say", "what_not_to_do"):
        assert k in ex["response"], k
    r = qa.post(f"{API}/exchanges/{ex['id']}/memories", json={"memories": [
        {"kind": "event", "text": "TEST he said hey"}, {"kind": "interpretation", "text": "TEST he is casual"}]})
    assert r.json()["saved"] == 2
    mems = [m for m in qa.get(f"{API}/people/{p['id']}/memories").json() if m["kind"] != "profile_detail"]
    ev = next(m for m in mems if m["kind"] == "event")
    interp = next(m for m in mems if m["kind"] == "interpretation")
    assert ev["id"] in interp["derived_from"]
    # edit
    assert qa.patch(f"{API}/memories/{ev['id']}", json={"text": "TEST he said hey (edited)"}).status_code == 200
    mems = qa.get(f"{API}/people/{p['id']}/memories").json()
    assert any(m.get("text") == "TEST he said hey (edited)" for m in mems)
    # delete event removes derived
    r = qa.delete(f"{API}/memories/{ev['id']}")
    assert r.status_code == 200 and r.json()["derived_removed"] == 1
    ids = {m["id"] for m in qa.get(f"{API}/people/{p['id']}/memories").json()}
    assert interp["id"] not in ids
    # delete exchange
    assert qa.delete(f"{API}/exchanges/{ex['id']}").status_code == 200
    assert all(e["id"] != ex["id"] for e in qa.get(f"{API}/people/{p['id']}/exchanges").json())


# archetype tag only for romantic
def test_archetype_tag_rules(qa):
    f = _person(qa, rel="friend", name="TEST_Friend")
    assert qa.patch(f"{API}/people/{f['id']}", json={"archetype_tag": 3}).status_code == 422
    c = _person(qa, rel="crush", name="TEST_Crush")
    r = qa.patch(f"{API}/people/{c['id']}", json={"archetype_tag": 3})
    assert r.status_code == 200 and r.json()["archetype_tag"] == 3
    assert qa.get(f"{API}/people/{c['id']}").json()["archetype_tag"] == 3
    r = qa.patch(f"{API}/people/{c['id']}", json={"clear_archetype": True, "archived": True})
    assert r.json()["archetype_tag"] is None and r.json()["archived"] is True


# export
def test_export(qa):
    d = qa.get(f"{API}/me/export").json()
    for k in ("account", "people", "memories", "exchanges", "preps", "patterns"):
        assert k in d
    assert all("_id" not in p for p in d["people"])


# admin waitlist toggle reflected in public config
def test_waitlist_toggle():
    a = _sess(ADMIN)
    orig = a.get(f"{API}/admin/settings").json()["waitlist_mode"]
    try:
        a.put(f"{API}/admin/settings", json={"waitlist_mode": True})
        assert requests.get(f"{API}/public/config").json()["waitlist_mode"] is True
    finally:
        a.put(f"{API}/admin/settings", json={"waitlist_mode": orig})
    assert requests.get(f"{API}/public/config").json()["waitlist_mode"] == orig


# validation: bad people_limit should be 4xx not 500
def test_admin_settings_bad_limit():
    a = _sess(ADMIN)
    r = a.put(f"{API}/admin/settings", json={"people_limit": "abc"})
    assert r.status_code in (400, 422), r.status_code
