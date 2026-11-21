# Iteration 17: quickstart, show-why fields, choice/correct/outcome loop, overview checkins, quick questions, practice
import json
import subprocess
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = f"tester+it17{uuid.uuid4().hex[:6]}_clerk_test@example.com"
T = 120


def _db():
    env = {}
    for line in open("/app/backend/.env"):
        if line.startswith(("MONGO_URL=", "DB_NAME=")):
            k, v = line.strip().split("=", 1)
            env[k] = v.strip('"')
    return MongoClient(env["MONGO_URL"])[env["DB_NAME"]]


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert sess.get(f"{API}/me").status_code == 200
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def qs(s):
    r = s.post(f"{API}/quickstart", timeout=T, json={
        "nickname": "TEST_Jay", "what_happened": "He stopped replying after our second date and then liked my story.",
        "conversation_text": "Jay: had a great time last night\nMe: me too! want to do it again?\nJay: maybe, busy week\nMe: no worries\nJay: haha"})
    assert r.status_code == 200, r.text
    return r.json()


# auth guards
def test_auth_guards():
    for m, p in [("post", "/quickstart"), ("get", "/me/next-question"), ("get", "/practice"), ("post", "/practice")]:
        assert getattr(requests, m)(f"{API}{p}", json={}).status_code in (401, 403)


# GET /me people_count
def test_me_people_count_zero(s):
    me = s.get(f"{API}/me").json()
    assert me["people_count"] == 0


# quickstart shape
def test_quickstart_shape(s, qs):
    assert qs["person"]["nickname"] == "TEST_Jay"
    ex = qs["exchange"]
    r = ex["response"]
    n = len(ex["conversation"])
    assert n >= 3
    assert 2 <= len(r["seen"]) <= 4
    assert len(r["explanations"]) >= 2
    assert all(e["likelihood"] in ("more likely", "possible", "less likely") for e in r["explanations"])
    assert 1 <= len(r["unknowns"]) <= 3
    assert 2 <= len(r["actions"]) <= 3 and all(a["label"] for a in r["actions"])
    for k in ("seen", "explanations"):
        for it in r[k]:
            assert all(isinstance(m, int) and 0 <= m < n for m in it["msgs"])
    assert "_id" not in ex
    assert s.get(f"{API}/me").json()["people_count"] == 1


def test_quickstart_validation(s):
    assert s.post(f"{API}/quickstart", json={"nickname": "", "what_happened": "x"}).status_code == 422
    assert s.post(f"{API}/quickstart", json={"nickname": "TEST_x", "what_happened": ""}).status_code == 422


# choice + overview checkins
def test_choice_and_checkins(s, qs):
    eid = qs["exchange"]["id"]
    r = s.post(f"{API}/exchanges/{eid}/choice", json={"label": "Wait a day"})
    assert r.status_code == 200 and r.json()["choice"]["label"] == "Wait a day"
    ov = s.get(f"{API}/overview").json()
    c = [x for x in ov["checkins"] if x["exchange_id"] == eid]
    assert c and c[0]["choice"] == "Wait a day"
    assert s.delete(f"{API}/exchanges/{eid}/choice").status_code == 200
    ov = s.get(f"{API}/overview").json()
    assert not [x for x in ov["checkins"] if x["exchange_id"] == eid]
    assert s.post(f"{API}/exchanges/{eid}/choice", json={"label": "My own plan", "custom": True}).status_code == 200
    assert s.post(f"{API}/exchanges/nope/choice", json={"label": "x"}).status_code == 404


# corrections
def test_correct_and_undo(s, qs):
    eid = qs["exchange"]["id"]
    db = _db()
    r = s.post(f"{API}/exchanges/{eid}/correct", json={"kind": "seen", "index": 0, "note": "TEST_wrong he did reply"})
    assert r.status_code == 200 and r.json()["corrected"] == "seen:0"
    exs = s.get(f"{API}/people/{qs['person']['id']}/exchanges").json()
    ex = [e for e in exs if e["id"] == eid][0]
    assert "seen:0" in ex["corrected"]
    assert db.corrections.find_one({"exchange_id": eid, "key": "seen:0"})["note"] == "TEST_wrong he did reply"
    assert s.post(f"{API}/exchanges/{eid}/correct", json={"kind": "explanations", "index": 1}).status_code == 200
    assert s.post(f"{API}/exchanges/{eid}/correct", json={"kind": "explanations", "index": 99}).status_code == 404
    assert s.post(f"{API}/exchanges/{eid}/correct", json={"kind": "bogus", "index": 0}).status_code == 422
    assert s.delete(f"{API}/exchanges/{eid}/correct", params={"kind": "explanations", "index": 1}).status_code == 200
    ex = [e for e in s.get(f"{API}/people/{qs['person']['id']}/exchanges").json() if e["id"] == eid][0]
    assert ex["corrected"] == ["seen:0"]
    assert db.corrections.find_one({"exchange_id": eid, "key": "explanations:1"}) is None


# outcome
def test_outcome_flow(s, qs):
    eid = qs["exchange"]["id"]
    assert s.post(f"{API}/exchanges/{eid}/outcome", json={}).status_code == 422
    assert s.post(f"{API}/exchanges/{eid}/outcome", json={"result": "weird"}).status_code == 422
    r = s.post(f"{API}/exchanges/{eid}/outcome", timeout=T, json={"result": "better", "text": "TEST_ he texted first next day"})
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["outcome_result"] == "better"
    assert "It went better" in ex["outcome"]
    assert ex["read_update"] is None or {"changed", "earlier", "now", "note"} <= set(ex["read_update"])
    ov = s.get(f"{API}/overview").json()
    assert not [x for x in ov["checkins"] if x["exchange_id"] == eid]
    assert s.delete(f"{API}/exchanges/{eid}/outcome").status_code == 200
    ex = [e for e in s.get(f"{API}/people/{qs['person']['id']}/exchanges").json() if e["id"] == eid][0]
    assert ex["outcome"] is None and ex["outcome_result"] is None
    r = s.post(f"{API}/exchanges/{eid}/outcome", timeout=T, json={"text": "TEST_ note only"})
    assert r.status_code == 200 and r.json()["outcome"] == "TEST_ note only"


# quick questions
def test_quick_questions(s):
    q1 = s.get(f"{API}/me/next-question").json()["question"]
    assert q1 and q1["id"] and len(q1["options"]) >= 2
    r = s.post(f"{API}/me/quick-answer", json={"qid": q1["id"], "choice": 1})
    assert r.status_code == 200
    q2 = r.json()["question"]
    assert q2["id"] != q1["id"]
    about = s.get(f"{API}/me/about").json()
    answers = about.get("answers") or (about.get("about") or {}).get("answers") or {}
    assert answers[q1["id"]]["choice"] == 1, about
    r = s.post(f"{API}/me/quick-answer", json={"qid": q2["id"], "choice": None})
    assert r.status_code == 200 and r.json()["question"]["id"] not in (q1["id"], q2["id"])
    assert s.post(f"{API}/me/quick-answer", json={"qid": "nope", "choice": 0}).status_code == 404
    assert s.post(f"{API}/me/quick-answer", json={"qid": q2["id"], "choice": 99}).status_code == 422


# practice live
def test_practice_live(s, qs):
    pid = qs["person"]["id"]
    assert s.post(f"{API}/practice", json={"person_id": pid, "topic": "x", "mode": "bad"}).status_code == 422
    assert s.post(f"{API}/practice", json={"person_id": "nope", "topic": "x", "mode": "live"}).status_code == 404
    r = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST_ ask where this is going", "mode": "live"})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert s.post(f"{API}/practice/{sid}/end").status_code == 422
    r = s.post(f"{API}/practice/{sid}/say", timeout=T, json={"text": "Hey, can we talk about what this is?"})
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["me", "them"] and msgs[1]["text"]
    r = s.post(f"{API}/practice/{sid}/end", timeout=T)
    assert r.status_code == 200, r.text
    fb = r.json()["feedback"]
    assert isinstance(fb["landed"], list) and isinstance(fb["backfire"], list) and fb["cleaned"]
    assert s.get(f"{API}/practice/{sid}").json()["ended"] is True
    r = s.post(f"{API}/practice/{sid}/restart")
    assert r.status_code == 200 and r.json()["messages"] == [] and r.json()["feedback"] is None
    lst = s.get(f"{API}/practice", params={"person_id": pid}).json()
    assert sid in [x["id"] for x in lst]
    assert s.delete(f"{API}/practice/{sid}").status_code == 200
    assert s.get(f"{API}/practice/{sid}").status_code == 404


# practice likely
def test_practice_likely(s, qs):
    pid = qs["person"]["id"]
    sid = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST_ meet my friends", "mode": "likely"}).json()["id"]
    r = s.post(f"{API}/practice/{sid}/say", timeout=T, json={"text": "Would you like to meet my friends Saturday?"})
    assert r.status_code == 200, r.text
    opts = r.json()["options"]
    assert len(opts) == 3 and all(o["reaction"] and o.get("suggested") for o in opts)
    assert s.post(f"{API}/practice/{sid}/pick", json={"index": 9}).status_code == 404
    r = s.post(f"{API}/practice/{sid}/pick", timeout=T, json={"index": 0})
    assert r.status_code == 200
    x = r.json()
    assert [m["role"] for m in x["messages"]] == ["me", "them", "me"]
    assert x["messages"][1]["text"] == opts[0]["reaction"]
    assert len(x["options"]) == 3
    assert s.delete(f"{API}/practice/{sid}").status_code == 200


# deleting person cascades practice + corrections
def test_delete_person_cascade(s, qs):
    pid = qs["person"]["id"]
    sid = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST_x", "mode": "live"}).json()["id"]
    assert s.delete(f"{API}/people/{pid}").status_code == 200
    assert s.get(f"{API}/practice/{sid}").status_code == 404
    assert _db().corrections.count_documents({"person_id": pid}) == 0
