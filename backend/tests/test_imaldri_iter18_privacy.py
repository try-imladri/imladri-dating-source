# Iteration 18: Privacy controls - per-person saved data, export, granular deletes, ownership, delete-all (/me)
import json
import subprocess
import uuid

import pytest
import requests

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
T = 150


def _sess(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert s.get(f"{API}/me").status_code == 200
    return s


@pytest.fixture(scope="module")
def s():
    sess = _sess(f"tester+it18a{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def other():
    sess = _sess(f"tester+it18b{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def qs(s):
    r = s.post(f"{API}/quickstart", timeout=T, json={
        "nickname": "TEST_Saved", "what_happened": "He stopped replying after I asked about meeting his friends.",
        "conversation_text": "Him: hey\nMe: are we still on for saturday with your friends?\nHim: not sure, busy week\nMe: ok"})
    assert r.status_code == 200, r.text
    return r.json()


# --- saved endpoint shape
def test_saved_shape(s, qs):
    pid = qs["person"]["id"]
    r = s.get(f"{API}/people/{pid}/saved")
    assert r.status_code == 200
    d = r.json()
    for k in ("person", "details", "report", "memories", "exchanges", "preps", "corrections", "practice", "group_chats"):
        assert k in d
    assert d["person"]["nickname"] == "TEST_Saved"
    assert any(x["key"] == "background" for x in d["details"])
    assert len(d["exchanges"]) >= 1
    assert d["exchanges"][0]["messages"] >= 2
    assert "\"_id\"" not in json.dumps(d)


# --- ownership
def test_saved_export_ownership(s, other, qs):
    pid = qs["person"]["id"]
    assert other.get(f"{API}/people/{pid}/saved").status_code == 404
    assert other.get(f"{API}/people/{pid}/export").status_code == 404
    assert other.delete(f"{API}/people/{pid}/report").status_code == 404
    assert requests.get(f"{API}/people/{pid}/saved").status_code in (401, 403)


# --- export
def test_person_export(s, qs):
    pid = qs["person"]["id"]
    r = s.get(f"{API}/people/{pid}/export")
    assert r.status_code == 200
    d = r.json()
    assert d["person"]["id"] == pid
    assert len(d["exchanges"]) >= 1
    for k in ("memories", "preps", "corrections", "practice", "group_chats"):
        assert isinstance(d[k], list)
    assert "\"_id\"" not in json.dumps(d)


# --- correction delete un-marks exchange; other user cannot delete
def test_correction_delete(s, other, qs):
    pid, eid = qs["person"]["id"], qs["exchange"]["id"]
    r = s.post(f"{API}/exchanges/{eid}/correct", json={"kind": "seen", "index": 0, "note": "TEST_off"})
    assert r.status_code == 200
    cors = s.get(f"{API}/people/{pid}/saved").json()["corrections"]
    assert len(cors) == 1 and cors[0]["note"] == "TEST_off"
    cid = cors[0]["id"]
    assert other.delete(f"{API}/corrections/{cid}").status_code == 404
    assert s.delete(f"{API}/corrections/{cid}").status_code == 200
    assert s.get(f"{API}/people/{pid}/saved").json()["corrections"] == []
    ex = s.get(f"{API}/people/{pid}/exchanges").json()
    exl = ex if isinstance(ex, list) else ex.get("exchanges", [])
    mine = [e for e in exl if e["id"] == eid]
    if mine:
        assert "seen:0" not in (mine[0].get("corrected") or [])
    assert s.delete(f"{API}/corrections/{cid}").status_code == 404


# --- memory, practice delete
def test_memory_and_practice_delete(s, qs):
    pid, eid = qs["person"]["id"], qs["exchange"]["id"]
    assert s.post(f"{API}/exchanges/{eid}/memories", json={"memories": [{"kind": "event", "text": "TEST_mem event"}]}).status_code == 200
    mems = s.get(f"{API}/people/{pid}/saved").json()["memories"]
    m = [x for x in mems if x["text"] == "TEST_mem event"]
    assert m
    assert s.delete(f"{API}/memories/{m[0]['id']}").status_code == 200
    assert not [x for x in s.get(f"{API}/people/{pid}/saved").json()["memories"] if x["id"] == m[0]["id"]]
    r = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST_topic", "mode": "likely"}, timeout=T)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    pr = s.get(f"{API}/people/{pid}/saved").json()["practice"]
    assert any(x["id"] == sid and x["topic"] == "TEST_topic" for x in pr)
    assert s.delete(f"{API}/practice/{sid}").status_code == 200
    assert not any(x["id"] == sid for x in s.get(f"{API}/people/{pid}/saved").json()["practice"])


# --- prep delete
def test_prep_delete(s, other, qs):
    pid = qs["person"]["id"]
    r = s.post(f"{API}/people/{pid}/preps", json={"situation": "TEST_ asking him about meeting friends"}, timeout=T)
    assert r.status_code == 200, r.text
    preps = s.get(f"{API}/people/{pid}/saved").json()["preps"]
    assert preps
    xid = preps[0]["id"]
    assert other.delete(f"{API}/preps/{xid}").status_code == 404
    assert s.delete(f"{API}/preps/{xid}").status_code == 200
    assert not any(x["id"] == xid for x in s.get(f"{API}/people/{pid}/saved").json()["preps"])
    assert s.delete(f"{API}/preps/{xid}").status_code == 404


# --- detail + report delete
def test_detail_and_report_delete(s, qs):
    pid = qs["person"]["id"]
    assert s.delete(f"{API}/people/{pid}/details/background").status_code == 200
    d = s.get(f"{API}/people/{pid}/saved").json()
    assert not any(x["key"] == "background" for x in d["details"])
    assert s.delete(f"{API}/people/{pid}/details/bogus").status_code == 404
    assert s.delete(f"{API}/people/{pid}/report").status_code == 200
    assert s.get(f"{API}/people/{pid}/saved").json()["report"] is None


# --- exchange delete then delete person
def test_exchange_and_person_delete(s, qs):
    pid, eid = qs["person"]["id"], qs["exchange"]["id"]
    assert s.delete(f"{API}/exchanges/{eid}").status_code == 200
    assert not any(e["id"] == eid for e in s.get(f"{API}/people/{pid}/saved").json()["exchanges"])
    assert s.delete(f"{API}/people/{pid}").status_code == 200
    assert s.get(f"{API}/people/{pid}/saved").status_code == 404


# --- account-wide export and wipe (throwaway account)
def test_me_export_and_wipe(other):
    r = other.post(f"{API}/people", json={"nickname": "TEST_Wipe", "rel_type": "friend", "stage": "good", "background": "TEST bg"})
    if r.status_code not in (200, 201):
        pytest.skip(f"create person failed {r.status_code} {r.text[:200]}")
    ex = other.get(f"{API}/me/export").json()
    assert any(p["nickname"] == "TEST_Wipe" for p in ex["people"])
    assert other.delete(f"{API}/me").status_code == 200
    assert other.get(f"{API}/me/export").json()["people"] == []
    me = other.get(f"{API}/me").json()
    assert me.get("onboarded") in (False, None)
