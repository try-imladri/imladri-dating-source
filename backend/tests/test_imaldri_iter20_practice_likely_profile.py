# Iteration 20: Practice likely mode (3 options + pick), to-profile -> patterns, live mode + end feedback, saved-data deletes
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
AI_LOG = "/app/test_reports/pytest/iter20_ai_outputs.json"
LOG = {}


def _sess(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert s.get(f"{API}/me").status_code == 200
    return s


@pytest.fixture(scope="module")
def s():
    sess = _sess(f"tester+it20a{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    json.dump(LOG, open(AI_LOG, "w"), indent=2)
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def other():
    sess = _sess(f"tester+it20b{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def person(s):
    r = s.post(f"{API}/quickstart", timeout=T, json={
        "nickname": "TEST_Sam", "what_happened": "Sam keeps cancelling plans last minute, and says work is crazy.",
        "conversation_text": "Sam: hey sorry cant do tonight, work is insane\nMe: again? ok\nSam: ill make it up to u promise\nMe: sure"})
    assert r.status_code == 200, r.text
    return r.json()["person"]


@pytest.fixture(scope="module")
def likely(s, person):
    r = s.post(f"{API}/practice", json={"person_id": person["id"], "topic": "I need more notice before plans get cancelled", "mode": "likely"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mode"] == "likely" and d["messages"] == [] and d["options"] == []
    return d


# --- likely mode: say returns 3 options with full fields
def test_likely_say_options(s, likely):
    r = s.post(f"{API}/practice/{likely['id']}/say", timeout=T, json={"text": "Hey, can we talk about plans? When they get cancelled last minute it really throws me off."})
    assert r.status_code == 200, r.text
    d = r.json()
    LOG["round1_options"] = d["options"]
    assert len(d["options"]) == 3
    for o in d["options"]:
        for k in ("reaction", "likelihood", "meaning", "outcome", "suggested"):
            assert isinstance(o.get(k), str) and o[k].strip(), (k, o)
    assert len({o["reaction"] for o in d["options"]}) == 3
    assert d["messages"] == [{"role": "me", "text": d["messages"][0]["text"]}]
    g = s.get(f"{API}/practice/{likely['id']}").json()
    assert len(g["options"]) == 3


# --- pick validation + append only their reaction
def test_likely_pick(s, likely):
    assert s.post(f"{API}/practice/{likely['id']}/pick", json={"index": 7}).status_code == 404
    assert s.post(f"{API}/practice/{likely['id']}/pick", json={"index": -1}).status_code == 404
    before = s.get(f"{API}/practice/{likely['id']}").json()
    r = s.post(f"{API}/practice/{likely['id']}/pick", json={"index": 1})
    assert r.status_code == 200
    d = r.json()
    assert d["options"] == []
    assert len(d["messages"]) == 2
    assert d["messages"][-1] == {"role": "them", "text": before["options"][1]["reaction"]}
    g = s.get(f"{API}/practice/{likely['id']}").json()
    assert g["options"] == [] and len(g["messages"]) == 2


# --- second round
def test_likely_round2(s, likely):
    r = s.post(f"{API}/practice/{likely['id']}/say", timeout=T, json={"text": "I get that work is busy. I just need a heads up earlier, like the day before."})
    assert r.status_code == 200
    d = r.json()
    LOG["round2_messages"] = d["messages"]
    LOG["round2_options"] = d["options"]
    assert len(d["options"]) == 3 and len(d["messages"]) == 3
    assert s.post(f"{API}/practice/{likely['id']}/pick", json={"index": 0}).json()["messages"][-1]["role"] == "them"


# --- to-profile: 422 with no "me" lines
def test_to_profile_422(s, person):
    x = s.post(f"{API}/practice", json={"person_id": person["id"], "topic": "TEST empty", "mode": "likely"}).json()
    r = s.post(f"{API}/practice/{x['id']}/to-profile", timeout=T)
    assert r.status_code == 422
    s.delete(f"{API}/practice/{x['id']}")


# --- to-profile saves insights in patterns
def test_to_profile(s, other, likely, person):
    assert other.post(f"{API}/practice/{likely['id']}/to-profile").status_code == 404
    r = s.post(f"{API}/practice/{likely['id']}/to-profile", timeout=T)
    assert r.status_code == 200, r.text
    saved = r.json()["saved"]
    LOG["to_profile"] = saved
    assert 1 <= len(saved) <= 3
    for it in saved:
        assert it["text"].startswith("You"), it
        assert it["merged"] is False
    g = s.get(f"{API}/practice/{likely['id']}").json()
    assert [x["id"] for x in g["profile_saved"]] == [x["id"] for x in saved]
    pats = {p["id"]: p for p in s.get(f"{API}/patterns").json()}
    for it in saved:
        p = pats[it["id"]]
        assert p["sources"] == [f"practice:{likely['id']}"]
        assert p["practice_count"] == 1 and p["source_count"] == 1
        assert p["people"] == ["TEST_Sam"]


# --- live mode + end feedback
def test_live_and_end(s, person):
    x = s.post(f"{API}/practice", json={"person_id": person["id"], "topic": "Ask Sam to commit to Saturday", "mode": "live"}).json()
    assert s.post(f"{API}/practice/{x['id']}/end", timeout=T).status_code == 422
    r = s.post(f"{API}/practice/{x['id']}/say", timeout=T, json={"text": "Are we actually doing Saturday? I want to plan around it."})
    assert r.status_code == 200
    d = r.json()
    assert len(d["messages"]) == 2 and d["messages"][1]["role"] == "them" and d["messages"][1]["text"].strip() not in ("", "…")
    assert d["options"] == []
    LOG["live_reply"] = d["messages"]
    e = s.post(f"{API}/practice/{x['id']}/end", timeout=T)
    assert e.status_code == 200
    fb = e.json()["feedback"]
    LOG["live_feedback"] = fb
    assert fb["landed"] and fb["backfire"] and fb["cleaned"]


# --- bad mode / ownership
def test_bad_mode_and_ownership(s, other, likely, person):
    assert s.post(f"{API}/practice", json={"person_id": person["id"], "topic": "x", "mode": "bogus"}).status_code == 422
    assert other.get(f"{API}/practice/{likely['id']}").status_code == 404
    assert other.post(f"{API}/practice/{likely['id']}/pick", json={"index": 0}).status_code == 404
    assert requests.post(f"{API}/practice/{likely['id']}/pick", json={"index": 0}).status_code in (401, 403)


# --- delete practice -> saved data no longer lists it; patterns still list
def test_delete_practice(s, likely, person):
    assert s.delete(f"{API}/practice/{likely['id']}").status_code == 200
    assert s.get(f"{API}/practice/{likely['id']}").status_code == 404
    saved = s.get(f"{API}/people/{person['id']}/saved").json()
    assert all(x["id"] != likely["id"] for x in saved["practice"])
    pats = s.get(f"{API}/patterns").json()
    LOG["patterns_after_practice_delete"] = pats
