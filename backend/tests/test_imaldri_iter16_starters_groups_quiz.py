# Iteration 16: further quiz f53-f60, about summary missing, starters (person + group), saved chat groups CRUD
import json
import subprocess
import time
import uuid

import pytest
import requests

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = f"tester+it16{uuid.uuid4().hex[:6]}_clerk_test@example.com"


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert sess.get(f"{API}/me").status_code == 200
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def people(s):
    ids = []
    for n, rt, st in [("TEST_Sam", "situationship", "early"), ("TEST_Mom", "mom", "good")]:
        r = s.post(f"{API}/people", json={"nickname": n, "rel_type": rt, "stage": st, "background": "met at work"})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    yield ids
    for i in ids:
        s.delete(f"{API}/people/{i}")


# auth guard on new endpoints
def test_unauth():
    assert requests.get(f"{API}/chat-groups").status_code in (401, 403)
    assert requests.get(f"{API}/people/x/starters").status_code in (401, 403)
    assert requests.post(f"{API}/group-chat/starters", json={"person_ids": ["a", "b"]}).status_code in (401, 403)


# quiz: 60 further questions with f53..f60
def test_quiz_further_60(s):
    q = s.get(f"{API}/quiz").json()
    ids = [x["id"] for x in q["further"]]
    assert len(ids) == 60, len(ids)
    for i in range(53, 61):
        assert f"f{i}" in ids
    dims = {x["id"]: x.get("dim") for x in q["further"]}
    assert dims["f53"] == "EI" and dims["f4"] == "JP" and dims["f56"] is None


# about summary missing list + answering f53 updates further_done and personality
def test_about_missing_and_answer(s):
    summ = s.get(f"{API}/me/about").json()["summary"]
    assert "missing" in summ
    m = summ["missing"]
    assert any("basics" in x for x in m), m
    assert "how your best friend describes you" in m
    assert any("60 Values & communication questions" in x for x in m), m
    before_ei = next(d for d in summ["personality"]["dims"] if d["pair"] == "EI")["answered"]
    r = s.put(f"{API}/me/about", json={"answers": {"f53": {"choice": 0, "note": ""}}})
    assert r.status_code == 200
    summ2 = r.json()["summary"]
    assert summ2["further_done"] == 1
    assert any("59 Values & communication questions" in x for x in summ2["missing"]), summ2["missing"]
    after_ei = next(d for d in summ2["personality"]["dims"] if d["pair"] == "EI")["answered"]
    assert after_ei == before_ei + 1
    assert summ2["score"] >= summ["score"]
    got = s.get(f"{API}/me/about").json()["about"]["answers"]["f53"]
    assert got["choice"] == 0


# person starters: 3 items, cached
def test_person_starters(s, people):
    t = time.time()
    r = s.get(f"{API}/people/{people[0]}/starters", timeout=90)
    assert r.status_code == 200, r.text
    items = r.json()["starters"]
    print("starters", items, round(time.time() - t, 1))
    assert len(items) == 3 and all(isinstance(x, str) and x for x in items)
    t = time.time()
    r2 = s.get(f"{API}/people/{people[0]}/starters", timeout=90)
    assert r2.json()["starters"] == items
    assert time.time() - t < 5
    assert s.get(f"{API}/people/nope/starters").status_code == 404


# group starters
def test_group_starters(s, people):
    r = s.post(f"{API}/group-chat/starters", json={"person_ids": people}, timeout=90)
    assert r.status_code == 200, r.text
    assert len(r.json()["starters"]) == 3
    assert s.post(f"{API}/group-chat/starters", json={"person_ids": [people[0]]}).status_code == 422
    assert s.post(f"{API}/group-chat/starters", json={"person_ids": [people[0], "nope"]}).status_code == 404


# saved chat groups CRUD
def test_chat_groups_crud(s, people):
    assert s.post(f"{API}/chat-groups", json={"name": "", "person_ids": people}).status_code == 422
    assert s.post(f"{API}/chat-groups", json={"name": "x", "person_ids": [people[0]]}).status_code == 422
    assert s.post(f"{API}/chat-groups", json={"name": "x", "person_ids": [people[0], "nope"]}).status_code == 404
    r = s.post(f"{API}/chat-groups", json={"name": "TEST_Fam", "person_ids": list(reversed(people))})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["name"] == "TEST_Fam" and g["person_ids"] == sorted(people) and "_id" not in g
    lst = s.get(f"{API}/chat-groups").json()
    assert any(x["id"] == g["id"] for x in lst)
    assert s.delete(f"{API}/chat-groups/{g['id']}").status_code == 200
    assert not any(x["id"] == g["id"] for x in s.get(f"{API}/chat-groups").json())
    assert s.delete(f"{API}/chat-groups/{g['id']}").status_code == 404
    assert s.delete(f"{API}/chat-groups/unknown").status_code == 404


# edge: whitespace-only name and duplicate ids
def test_chat_groups_edge(s, people):
    r = s.post(f"{API}/chat-groups", json={"name": "   ", "person_ids": people})
    print("whitespace name", r.status_code, r.text[:200])
    if r.status_code == 200:
        s.delete(f"{API}/chat-groups/{r.json()['id']}")
    r2 = s.post(f"{API}/chat-groups", json={"name": "dup", "person_ids": [people[0], people[0]]})
    print("dup ids", r2.status_code, r2.text[:200])
    if r2.status_code == 200:
        s.delete(f"{API}/chat-groups/{r2.json()['id']}")


# deleting a person prunes groups
def test_person_delete_prunes_group(s):
    a = s.post(f"{API}/people", json={"nickname": "TEST_A", "rel_type": "mom", "stage": "good", "background": "x"}).json()["id"]
    b = s.post(f"{API}/people", json={"nickname": "TEST_B", "rel_type": "mom", "stage": "good", "background": "x"}).json()["id"]
    g = s.post(f"{API}/chat-groups", json={"name": "TEST_AB", "person_ids": [a, b]}).json()
    s.delete(f"{API}/people/{a}")
    assert not any(x["id"] == g["id"] for x in s.get(f"{API}/chat-groups").json())
    s.delete(f"{API}/people/{b}")
