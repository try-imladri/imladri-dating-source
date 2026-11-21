# Iteration 15: patterns scan (empty + after chat), group-chat CRUD, type-compare people chips, auth guards
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
EMAIL = f"tester+it15{uuid.uuid4().hex[:6]}_clerk_test@example.com"


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
        assert r.json()["nickname"] == n
        ids.append(r.json()["id"])
    yield ids
    for i in ids:
        s.delete(f"{API}/people/{i}")


# auth guard
def test_unauth_401():
    for path in ["/patterns", "/group-chat?ids=a,b", "/me/type-compare"]:
        assert requests.get(f"{API}{path}").status_code in (401, 403)
    assert requests.post(f"{API}/patterns/scan").status_code in (401, 403)


# patterns scan with no chats
def test_scan_no_chats(s, people):
    r = s.post(f"{API}/patterns/scan", timeout=60)
    assert r.status_code == 422
    assert r.json()["detail"] == "Share a conversation or ask about someone first"
    assert s.get(f"{API}/patterns").json() == []


# group chat validation
def test_group_chat_validation(s, people):
    assert s.post(f"{API}/group-chat", json={"person_ids": [people[0]], "question": "hi"}).status_code == 422
    assert s.post(f"{API}/group-chat", json={"person_ids": people, "question": ""}).status_code == 422
    assert s.post(f"{API}/group-chat", json={"person_ids": [people[0], "nope"], "question": "hi"}).status_code == 404


# group chat create/list/delete
def test_group_chat_crud(s, people):
    r = s.post(f"{API}/group-chat", json={"person_ids": people, "question": "Who should I reply to first this weekend?"}, timeout=150)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "_id" not in d and d["question"].startswith("Who should")
    assert isinstance(d["response"], dict) and len(d["response"]) >= 4
    print("group keys", list(d["response"].keys()))
    ids_rev = ",".join(reversed(people))
    lst = s.get(f"{API}/group-chat?ids={ids_rev}").json()
    assert [x["id"] for x in lst] == [d["id"]]
    assert s.delete(f"{API}/group-chat/{d['id']}").json() == {"ok": True}
    assert s.get(f"{API}/group-chat?ids={ids_rev}").json() == []


# single chat -> patterns scan -> list shows sources/people
def test_chat_then_scan(s, people):
    r = s.post(f"{API}/people/{people[0]}/exchanges", json={
        "conversation": [{"speaker": "them", "text": "hey sorry been busy"}, {"speaker": "me", "text": "no worries!! totally fine, whenever you're free, I'm always around"}],
        "question": "I always say it's fine even when it isn't. Should I reply?"}, timeout=150)
    assert r.status_code == 200, r.text
    ex = r.json()
    print("exchange keys", list(ex.keys()))
    r = s.post(f"{API}/patterns/scan", timeout=150)
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["added"], int)
    pats = s.get(f"{API}/patterns").json()
    print("patterns", pats)
    for p in pats:
        assert p["source_count"] >= 1 and "_id" not in p
    if pats:
        assert any("TEST_Sam" in p["people"] for p in pats)


# type-compare marks tagged romantic people only
def test_type_compare_people_chip(s, people):
    assert s.put(f"{API}/me/about", json={"mbti": "INFJ", "answers": {}}).status_code == 200
    assert s.patch(f"{API}/people/{people[0]}", json={"archetype_tag": 5}).status_code == 200
    r = s.get(f"{API}/me/type-compare?gender=male", timeout=150)
    assert r.status_code == 200, r.text
    items = {i["number"]: i for i in r.json()["items"]}
    assert 5 in items
    assert [p["id"] for p in items[5]["people"]] == [people[0]]
    assert all(not i["people"] for n, i in items.items() if n != 5)
