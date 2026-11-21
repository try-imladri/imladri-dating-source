# Iteration 21: likely /say 409 guard, option ordering, you-voice, pattern source cleanup on practice/person delete
import json
import re
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
LOG = {}
ORDER = {"more likely": 0, "possible": 1, "less likely": 2}


def _sess(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert s.get(f"{API}/me").status_code == 200
    return s


@pytest.fixture(scope="module")
def s():
    sess = _sess(f"tester+it21{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    json.dump(LOG, open("/app/test_reports/pytest/iter21_ai_outputs.json", "w"), indent=2)
    sess.delete(f"{API}/me")


def _person(s, name):
    r = s.post(f"{API}/people", json={"nickname": name, "rel_type": "friend", "stage": "good", "background": "Old friend who cancels plans last minute a lot."})
    assert r.status_code == 200, r.text
    return r.json()


def _likely_round(s, pid, topic):
    x = s.post(f"{API}/practice", json={"person_id": pid, "topic": topic, "mode": "likely"}).json()
    r = s.post(f"{API}/practice/{x['id']}/say", timeout=T, json={"text": "Hey, I need to talk about how plans keep getting cancelled last minute. It hurts."})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def p1(s):
    return _person(s, "TEST_Kai21")


@pytest.fixture(scope="module")
def sess1(s, p1):
    return _likely_round(s, p1["id"], "I need more notice before plans get cancelled")


# --- 409 while options pending; then works after pick
def test_say_409_while_pending(s, sess1):
    assert len(sess1["options"]) == 3
    r = s.post(f"{API}/practice/{sess1['id']}/say", json={"text": "another line"})
    assert r.status_code == 409, r.text
    assert "Pick" in r.json().get("detail", "")
    g = s.get(f"{API}/practice/{sess1['id']}").json()
    assert len(g["messages"]) == 1 and len(g["options"]) == 3


# --- options sorted most likely first, meaning/outcome use "you" not "she"
def test_options_sorted_and_you_voice(s, sess1):
    opts = sess1["options"]
    LOG["options"] = opts
    ranks = [ORDER.get(o["likelihood"].strip().lower(), 1) for o in opts]
    assert ranks == sorted(ranks), [o["likelihood"] for o in opts]
    for o in opts:
        assert o["likelihood"] in ORDER, o["likelihood"]
        for k in ("meaning", "outcome"):
            assert not re.search(r"\bshe\b|\bher\b", o[k], re.I), (k, o[k])


# --- delete session removes practice:<sid> source and orphan patterns
def test_delete_practice_cleans_patterns(s, sess1):
    sid = sess1["id"]
    assert s.post(f"{API}/practice/{sid}/pick", json={"index": 0}).status_code == 200
    r = s.post(f"{API}/practice/{sid}/to-profile", timeout=T)
    assert r.status_code == 200, r.text
    saved = r.json()["saved"]
    LOG["to_profile"] = saved
    assert saved
    ids = {x["id"] for x in saved}
    pats = {p["id"]: p for p in s.get(f"{API}/patterns").json()}
    assert all(f"practice:{sid}" in pats[i]["sources"] for i in ids)
    assert s.delete(f"{API}/practice/{sid}").status_code == 200
    pats2 = s.get(f"{API}/patterns").json()
    assert not any(f"practice:{sid}" in p.get("sources", []) for p in pats2)
    assert not (ids & {p["id"] for p in pats2}), "orphan patterns not deleted"


# --- delete person removes their practice sources/patterns, keeps other person's
def test_delete_person_cleans_patterns(s):
    a = _person(s, "TEST_Ash21")
    b = _person(s, "TEST_Bo21")
    xa = _likely_round(s, a["id"], "Ask for more notice")
    s.post(f"{API}/practice/{xa['id']}/pick", json={"index": 0})
    xb = _likely_round(s, b["id"], "Ask for more notice")
    s.post(f"{API}/practice/{xb['id']}/pick", json={"index": 0})
    sa = s.post(f"{API}/practice/{xa['id']}/to-profile", timeout=T).json()["saved"]
    sb = s.post(f"{API}/practice/{xb['id']}/to-profile", timeout=T).json()["saved"]
    LOG["to_profile_a"], LOG["to_profile_b"] = sa, sb
    assert s.delete(f"{API}/people/{a['id']}").status_code == 200
    pats = {p["id"]: p for p in s.get(f"{API}/patterns").json()}
    assert not any(f"practice:{xa['id']}" in p.get("sources", []) for p in pats.values())
    for it in sa:
        if not it["merged"] and it["id"] not in {x["id"] for x in sb}:
            assert it["id"] not in pats
    for it in sb:
        assert it["id"] in pats and f"practice:{xb['id']}" in pats[it["id"]]["sources"]
    assert s.get(f"{API}/practice/{xa['id']}").status_code == 404
    s.delete(f"{API}/people/{b['id']}")


# --- edited practice-derived pattern: what happens on session delete (exchange delete keeps edited ones)
def test_edited_pattern_on_session_delete(s, p1):
    x = _likely_round(s, p1["id"], "Talk about weekend plans")
    s.post(f"{API}/practice/{x['id']}/pick", json={"index": 0})
    saved = s.post(f"{API}/practice/{x['id']}/to-profile", timeout=T).json()["saved"]
    pid = saved[0]["id"]
    assert s.patch(f"{API}/patterns/{pid}", json={"text": "TEST_ my own edited pattern"}).status_code == 200
    s.delete(f"{API}/practice/{x['id']}")
    ids = {p["id"] for p in s.get(f"{API}/patterns").json()}
    LOG["edited_pattern_survives"] = pid in ids
    assert pid in ids, "user-edited pattern deleted with practice session (exchange delete keeps edited patterns)"
