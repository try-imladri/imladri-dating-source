# Iteration 23: Practice Retry Branch - POST /api/practice/{sid}/branch rewinds to a round with a chosen option
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
T = 180


def _sess(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert s.get(f"{API}/me").status_code == 200
    return s


@pytest.fixture(scope="module")
def s():
    sess = _sess(f"tester+it23{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def other():
    sess = _sess(f"tester+it23o{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def pid(s):
    r = s.post(f"{API}/people", json={"nickname": "TEST_Rio23", "rel_type": "friend", "stage": "good", "background": "Cancels plans last minute."})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _say(s, sid, text):
    r = s.post(f"{API}/practice/{sid}/say", json={"text": text}, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def ended(s, pid):
    x = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST notice", "mode": "likely"}).json()
    sid = x["id"]
    lines = ["Hey, can we talk about plans getting cancelled?", "I need a heads up the day before."]
    for ln in lines:
        assert len(_say(s, sid, ln)["options"]) == 3
        assert s.post(f"{API}/practice/{sid}/pick", json={"index": 0}).status_code == 200
    e = s.post(f"{API}/practice/{sid}/end", timeout=T)
    assert e.status_code == 200, e.text
    return sid, lines, e.json()


def test_branch_round0(s, ended):
    sid, lines, ed = ended
    opt = ed["rounds"][0]["options"][2]
    r = s.post(f"{API}/practice/{sid}/branch", json={"round": 0, "index": 2})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["messages"] == [{"role": "me", "text": lines[0]}, {"role": "them", "text": opt["reaction"]}]
    assert b["ended"] is False and b["feedback"] is None and b["options"] == []
    assert len(b["rounds"]) == 1 and b["rounds"][0]["picked"] == 2
    g = s.get(f"{API}/practice/{sid}").json()
    assert g["messages"] == b["messages"] and len(g["rounds"]) == 1 and g["ended"] is False
    # continue a new round from the branch
    y = _say(s, sid, "OK, that's fair. Can we try next week?")
    assert len(y["options"]) == 3
    z = s.post(f"{API}/practice/{sid}/pick", json={"index": 1}).json()
    assert len(z["rounds"]) == 2 and z["rounds"][1]["line"] == "OK, that's fair. Can we try next week?"
    assert len(z["messages"]) == 4


def test_branch_bad_round_index(s, ended):
    sid, _, _ = ended
    assert s.post(f"{API}/practice/{sid}/branch", json={"round": 9, "index": 0}).status_code == 404
    assert s.post(f"{API}/practice/{sid}/branch", json={"round": 0, "index": 7}).status_code == 404
    assert s.post(f"{API}/practice/{sid}/branch", json={"round": -1, "index": 0}).status_code == 404
    assert s.post(f"{API}/practice/{sid}/branch", json={"round": 0, "index": -1}).status_code == 404


def test_branch_other_user_404(other, ended):
    sid, _, _ = ended
    assert other.post(f"{API}/practice/{sid}/branch", json={"round": 0, "index": 0}).status_code == 404


def test_branch_unauth_401(ended):
    sid, _, _ = ended
    assert requests.post(f"{API}/practice/{sid}/branch", json={"round": 0, "index": 0}).status_code == 401


def test_branch_missing_session_404(s):
    assert s.post(f"{API}/practice/nope-{uuid.uuid4().hex}/branch", json={"round": 0, "index": 0}).status_code == 404


# Edge: end with a pending round, then send a new line -> rounds/me-lines alignment
def test_branch_after_resume_from_pending_round(s, pid):
    x = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST align", "mode": "likely"}).json()
    sid = x["id"]
    _say(s, sid, "Line A")
    s.post(f"{API}/practice/{sid}/pick", json={"index": 0})
    _say(s, sid, "Line B")  # pending
    assert s.post(f"{API}/practice/{sid}/end", timeout=T).status_code == 200
    _say(s, sid, "Line C")  # pending B round dropped, B 'me' line stays
    z = s.post(f"{API}/practice/{sid}/pick", json={"index": 0}).json()
    assert [r["line"] for r in z["rounds"]] == ["Line A", "Line C"]
    opt = z["rounds"][1]["options"][1]
    b = s.post(f"{API}/practice/{sid}/branch", json={"round": 1, "index": 1}).json()
    # rewinding round 1 should keep "Line C" as the last me line
    me_lines = [m["text"] for m in b["messages"] if m["role"] == "me"]
    assert me_lines[-1] == "Line C", f"branch cut at wrong line: {b['messages']}"
    assert b["messages"][-1]["text"] == opt["reaction"]
