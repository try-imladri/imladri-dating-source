# Iteration 22: Practice Replay - /pick stores rounds, /end appends pending round, /restart clears, live has no rounds
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
    sess = _sess(f"tester+it22{uuid.uuid4().hex[:6]}_clerk_test@example.com")
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def pid(s):
    r = s.post(f"{API}/people", json={"nickname": "TEST_Rio22", "rel_type": "friend", "stage": "good", "background": "Cancels plans last minute."})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _say(s, sid, text):
    r = s.post(f"{API}/practice/{sid}/say", json={"text": text}, timeout=T)
    assert r.status_code == 200, r.text
    return r.json()


# --- two picked rounds + pending round appended at end + restart clears
def test_replay_rounds_flow(s, pid):
    x = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST more notice", "mode": "likely"}).json()
    sid = x["id"]
    lines = ["Hey, can we talk about plans getting cancelled?", "I need a heads up the day before, that's all.", "Does that sound fair?"]
    picks = [0, 2]
    for i, ln in enumerate(lines[:2]):
        y = _say(s, sid, ln)
        assert len(y["options"]) == 3
        opts = y["options"]
        r = s.post(f"{API}/practice/{sid}/pick", json={"index": picks[i]})
        assert r.status_code == 200, r.text
        z = r.json()
        assert z["options"] == []
        assert len(z["rounds"]) == i + 1
        rd = z["rounds"][i]
        assert rd["line"] == ln and rd["picked"] == picks[i] and len(rd["options"]) == 3
        assert rd["options"][picks[i]]["reaction"] == opts[picks[i]]["reaction"]
        assert z["messages"][-1] == {"role": "them", "text": opts[picks[i]]["reaction"]}
    # persisted
    g = s.get(f"{API}/practice/{sid}").json()
    assert len(g["rounds"]) == 2
    # pending round
    y = _say(s, sid, lines[2])
    assert len(y["options"]) == 3
    e = s.post(f"{API}/practice/{sid}/end", timeout=T)
    assert e.status_code == 200, e.text
    ed = e.json()
    assert ed["ended"] is True and ed["options"] == []
    assert ed["feedback"]["landed"]
    assert len(ed["rounds"]) == 3
    assert ed["rounds"][2]["picked"] is None and ed["rounds"][2]["line"] == lines[2]
    g = s.get(f"{API}/practice/{sid}").json()
    assert len(g["rounds"]) == 3 and g["ended"] is True
    # end again does not duplicate pending round
    e2 = s.post(f"{API}/practice/{sid}/end", timeout=T).json()
    assert len(e2["rounds"]) == 3
    # restart clears
    rr = s.post(f"{API}/practice/{sid}/restart").json()
    assert rr["rounds"] == [] and rr["messages"] == [] and rr["ended"] is False and rr["feedback"] is None


def test_pick_invalid_index(s, pid):
    x = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST idx", "mode": "likely"}).json()
    assert s.post(f"{API}/practice/{x['id']}/pick", json={"index": 0}).status_code == 404
    assert s.post(f"{API}/practice/{x['id']}/pick", json={"index": -1}).status_code == 404


def test_live_has_no_rounds(s, pid):
    x = s.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST live", "mode": "live"}).json()
    y = _say(s, x["id"], "Hey, can we talk about plans?")
    assert y["messages"][-1]["role"] == "them"
    e = s.post(f"{API}/practice/{x['id']}/end", timeout=T).json()
    assert (e.get("rounds") or []) == []


def test_unauth_401():
    assert requests.get(f"{API}/practice").status_code == 401
