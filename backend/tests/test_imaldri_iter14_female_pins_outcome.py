# Iteration 14: female archetypes (public list/page), female tag on person, type-compare by gender, prep pin + outcome -> memory + report regen
import json
import subprocess
import time

import pytest
import requests

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = "tester+clerk_test@example.com"
CREATED = []


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    yield sess
    for pid in CREATED:
        sess.delete(f"{API}/people/{pid}")


@pytest.fixture(scope="module")
def archs():
    r = requests.get(f"{API}/public/archetypes")
    assert r.status_code == 200
    return r.json()


# --- public archetypes ---
def test_catalog_with_gender_editions(archs):
    assert len(archs) == 115
    assert all(a.get("gender") in ("male", "female") for a in archs)
    m = [a for a in archs if a["gender"] == "male"]
    f = [a for a in archs if a["gender"] == "female"]
    assert len(m) == 56 and len(f) == 59
    assert all(a.get("ready") is True and a.get("unlocked") for a in f)
    assert sorted(a["number"] for a in f) == list(range(101, 160))
    assert len({a["slug"] for a in archs}) == 115


def test_female_page_and_cousin(archs):
    r = requests.get(f"{API}/public/archetypes/cool-girl")
    assert r.status_code == 200
    d = r.json()
    assert d["gender"] == "female" and d["number"] == 101
    sec = d["sections"]
    for k in ("summary", "texting_patterns", "red_flags", "closest_cousin"):
        assert sec.get(k), k
    assert sec["texting_patterns"].get("bubbles")
    assert d.get("cousin_ref") and d["cousin_ref"]["slug"]
    c = requests.get(f"{API}/public/archetypes/{d['cousin_ref']['slug']}").json()
    assert c["gender"] == "female"


def test_all_female_cousins_are_female(archs):
    fnums = {a["number"] for a in archs if a["gender"] == "female"}
    bad = []
    for a in [x for x in archs if x["gender"] == "female"]:
        d = requests.get(f"{API}/public/archetypes/{a['slug']}").json()
        n = ((d.get("sections") or {}).get("closest_cousin") or {}).get("number")
        if n not in fnums or n == a["number"]:
            bad.append((a["slug"], n))
    assert not bad, bad


def test_male_page_unchanged():
    d = requests.get(f"{API}/public/archetypes/love-bomber").json()
    assert d.get("gender", "male") == "male" and d["number"] <= 47 and d["sections"]


# --- auth ---
def test_auth_required():
    assert requests.get(f"{API}/me/type-compare?gender=female").status_code == 401
    assert requests.patch(f"{API}/preps/x", json={"pinned": True}).status_code == 401


# --- person female tag ---
@pytest.fixture(scope="module")
def pid(s):
    r = s.post(f"{API}/people", json={"nickname": "TEST_Fem", "rel_type": "crush", "stage": "early", "background": "met at a bookshop"})
    assert r.status_code in (200, 201), r.text
    CREATED.append(r.json()["id"])
    return r.json()["id"]


def test_female_tag_persists(s, pid):
    r = s.patch(f"{API}/people/{pid}", json={"archetype_tag": 101})
    assert r.status_code == 200, r.text
    assert s.get(f"{API}/people/{pid}").json()["archetype_tag"] == 101


# --- type compare ---
def test_type_compare_female(s, archs):
    r = s.get(f"{API}/me/type-compare?gender=female", timeout=120)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["gender"] == "female"
    if not d["code"]:
        pytest.skip("tester has no personality type")
    fnums = {a["number"] for a in archs if a["gender"] == "female"}
    assert d["items"] and all(i["number"] in fnums for i in d["items"])
    m = s.get(f"{API}/me/type-compare?gender=male", timeout=120).json()
    assert m["gender"] == "male" and m["items"] and all(i["number"] <= 47 for i in m["items"])
    t = time.time()
    again = s.get(f"{API}/me/type-compare?gender=female", timeout=60).json()
    assert time.time() - t < 5 and [i["number"] for i in again["items"]] == [i["number"] for i in d["items"]]


# --- preps: pin + outcome ---
@pytest.fixture(scope="module")
def preps(s, pid):
    ids = []
    for sit in ("TEST_ coffee on Saturday", "TEST_ asking about exclusivity"):
        r = s.post(f"{API}/people/{pid}/preps", json={"situation": sit}, timeout=120)
        assert r.status_code == 200, r.text
        assert r.json()["pinned"] is False and r.json()["outcome"] is None
        ids.append(r.json()["id"])
    return ids


def test_prep_based_on_shows_female_tag(s, pid, preps):
    lst = s.get(f"{API}/people/{pid}/preps").json()
    assert any("archetype #01 (your tag)" in lst[0]["based_on"] for _ in [0]), lst[0]["based_on"]


def test_pin_moves_to_top_and_unpin(s, pid, preps):
    older = preps[0]
    lst = s.get(f"{API}/people/{pid}/preps").json()
    assert lst[0]["id"] == preps[1]
    r = s.patch(f"{API}/preps/{older}", json={"pinned": True})
    assert r.status_code == 200 and r.json()["pinned"] is True
    lst = s.get(f"{API}/people/{pid}/preps").json()
    assert lst[0]["id"] == older and lst[0]["pinned"] is True
    s.patch(f"{API}/preps/{older}", json={"pinned": False})
    lst = s.get(f"{API}/people/{pid}/preps").json()
    assert lst[0]["id"] == preps[1] and lst[1]["pinned"] is False


def test_pin_validation_and_404(s, preps):
    assert s.patch(f"{API}/preps/{preps[0]}", json={}).status_code == 422
    assert s.patch(f"{API}/preps/nope", json={"pinned": True}).status_code == 404
    assert s.post(f"{API}/preps/nope/outcome", json={"text": "x"}).status_code == 404


def test_outcome_empty_rejected(s, preps):
    assert s.post(f"{API}/preps/{preps[0]}/outcome", json={"text": "   "}).status_code == 422


def test_outcome_memory_and_report(s, pid, preps):
    r = s.post(f"{API}/preps/{preps[0]}/outcome", json={"text": "TEST_ she said yes to coffee and texted after"})
    assert r.status_code == 200, r.text
    lst = s.get(f"{API}/people/{pid}/preps").json()
    x = [y for y in lst if y["id"] == preps[0]][0]
    assert x["outcome"].startswith("TEST_ she said yes")
    mems = s.get(f"{API}/people/{pid}/memories").json()
    outs = [m for m in mems if m.get("kind") == "outcome" and m.get("prep_id") == preps[0]]
    assert len(outs) == 1
    # editing outcome replaces memory, not duplicates
    s.post(f"{API}/preps/{preps[0]}/outcome", json={"text": "TEST_ edited outcome"})
    mems = s.get(f"{API}/people/{pid}/memories").json()
    outs = [m for m in mems if m.get("kind") == "outcome" and m.get("prep_id") == preps[0]]
    assert len(outs) == 1 and outs[0]["text"] == "TEST_ edited outcome"
    statuses = []
    for _ in range(40):
        st = s.get(f"{API}/people/{pid}").json().get("report_status")
        statuses.append(st)
        if st == "ready":
            break
        time.sleep(3)
    assert statuses[-1] == "ready", statuses
    p = s.get(f"{API}/people/{pid}").json()
    assert p.get("report")
