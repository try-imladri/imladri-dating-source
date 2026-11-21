# Iteration 13: PATCH /people validation, goal->intake.her_goal sync, delete-during-AI race (exchanges/preps), Based on has no '?'
import json
import subprocess
import threading
import time

import pytest
import requests
from pymongo import MongoClient

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = "tester+clerk_test@example.com"
CREATED = []


def _mongo():
    # local mongo; pick the db that holds the people collection (no backend/.env access)
    c = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
    for n in c.list_database_names():
        if "people" in c[n].list_collection_names() and "exchanges" in c[n].list_collection_names():
            return c[n]
    pytest.skip("no app db found")


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    yield sess
    for pid in CREATED:
        sess.delete(f"{API}/people/{pid}")


def _mk(s, name="TEST_Val", rel="crush", stage="early", goal=None):
    body = {"nickname": name, "rel_type": rel, "stage": stage, "background": "met at gym"}
    if goal:
        body["goal"] = goal
    r = s.post(f"{API}/people", json=body)
    assert r.status_code in (200, 201), r.text
    CREATED.append(r.json()["id"])
    return r.json()["id"]


@pytest.fixture(scope="module")
def pid(s):
    return _mk(s, goal="see where it goes")


@pytest.mark.parametrize("body", [{"nickname": ""}, {"nickname": "   "}, {"background": ""}, {"background": "  "}, {"stage": "bogus"}, {"stage": "good"}])
def test_patch_invalid_422(s, pid, body):
    r = s.patch(f"{API}/people/{pid}", json=body)
    assert r.status_code == 422, (body, r.status_code, r.text)
    g = s.get(f"{API}/people/{pid}").json()
    assert g["nickname"] == "TEST_Val" and g["stage"] == "early" and g["background"] == "met at gym"


def test_patch_valid_stage_200(s, pid):
    r = s.patch(f"{API}/people/{pid}", json={"stage": "established"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "established"
    assert s.get(f"{API}/people/{pid}").json()["stage"] == "established"


def test_patch_valid_stage_other_reltype(s):
    p2 = _mk(s, "TEST_Friend", rel="friend", stage="good")
    assert s.patch(f"{API}/people/{p2}", json={"stage": "crush"}).status_code == 422
    r = s.patch(f"{API}/people/{p2}", json={"stage": "strained"})
    assert r.status_code == 200 and r.json()["stage"] == "strained"


def test_patch_goal_syncs_intake(s, pid):
    assert s.get(f"{API}/people/{pid}").json()["intake"].get("her_goal") == "see where it goes"
    r = s.patch(f"{API}/people/{pid}", json={"goal": "TEST_ find out if serious"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["goal"] == "TEST_ find out if serious" and d["intake"]["her_goal"] == "TEST_ find out if serious"
    assert "meter" in d and "_id" not in d
    g = s.get(f"{API}/people/{pid}").json()
    assert g["goal"] == g["intake"]["her_goal"] == "TEST_ find out if serious"


def test_patch_nickname_trim_ok(s, pid):
    r = s.patch(f"{API}/people/{pid}", json={"nickname": "TEST_Renamed"})
    assert r.status_code == 200 and r.json()["nickname"] == "TEST_Renamed"


def test_based_on_no_question_mark(s, pid):
    r = s.patch(f"{API}/people/{pid}/intake", json={"fields": {"known_for": "about two years now", "current_state": "talking daily"}})
    assert r.status_code == 200, r.text
    r = s.post(f"{API}/people/{pid}/preps", json={"situation": "TEST_ dinner on friday"}, timeout=120)
    assert r.status_code == 200, r.text
    bo = r.json()["based_on"]
    print("based_on:", bo)
    assert all("?" not in x for x in bo), bo
    assert any("how long have you known them" in x for x in bo)


def _race(s, path, payload):
    p = _mk(s, "TEST_Race")
    res = {}

    def call():
        res["r"] = s.post(f"{API}/people/{p}/{path}", json=payload, timeout=120)

    t = threading.Thread(target=call)
    t.start()
    time.sleep(2.5)
    d = s.delete(f"{API}/people/{p}")
    assert d.status_code == 200
    t.join()
    return p, res["r"]


def test_race_exchange(s):
    p, r = _race(s, "exchanges", {"question": "TEST_ should I text first?"})
    assert r.status_code == 404, (r.status_code, r.text[:200])
    db = _mongo()
    assert db.exchanges.count_documents({"person_id": p}) == 0
    assert db.people.count_documents({"id": p}) == 0


def test_race_prep(s):
    p, r = _race(s, "preps", {"situation": "TEST_ coffee tomorrow"})
    assert r.status_code == 404, (r.status_code, r.text[:200])
    db = _mongo()
    assert db.preps.count_documents({"person_id": p}) == 0
