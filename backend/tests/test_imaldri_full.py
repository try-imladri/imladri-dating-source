"""imaldri full API regression (iteration 1 of testing agent): public, auth, people, AI exchange, memories, outcome, preps, notifications, limits, admin RBAC.
Uses tester3+clerk_test@example.com (expects 0 people at start) so free-tier limit test is deterministic."""
import json
import os
import subprocess
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"
USER = "tester3+clerk_test@example.com"
MEMBER = "tester+clerk_test@example.com"
ADMIN = "tester+admin_clerk_test@example.com"
SIX = ("what_they_are_doing", "what_it_means", "what_changed", "best_move", "what_to_say", "what_not_to_do")


def _sess(email):
    tok = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))["token"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def u():
    return _sess(USER)


@pytest.fixture(scope="module")
def created(u):
    ids = []
    yield ids
    for pid in ids:
        u.delete(f"{API}/people/{pid}")


@pytest.fixture(scope="module")
def state():
    return {}


# Public content
def test_public_archetypes_47():
    d = requests.get(f"{API}/public/archetypes").json()
    assert len(d) == 47
    assert [x["number"] for x in d] == list(range(1, 48))
    assert all(x.get("name") and x.get("one_liner") for x in d)


def test_public_figures_with_match_names():
    d = requests.get(f"{API}/public/figures").json()
    assert d
    for f in d:
        assert f["matches"], f["slug"]
        assert all(m.get("name") for m in f["matches"]), f["slug"]


def test_love_bomber_and_elon():
    a = requests.get(f"{API}/public/archetypes/love-bomber")
    assert a.status_code == 200 and a.json()["name"]
    f = requests.get(f"{API}/public/figures/elon-musk")
    assert f.status_code == 200 and f.json()["sections"]


# Auth
def test_no_token_401():
    assert requests.get(f"{API}/people").status_code == 401
    assert requests.get(f"{API}/notifications").status_code == 401


# People + 6-part AI
def test_create_person(u, created, state):
    r = u.post(f"{API}/people", json={"nickname": "TEST_Ryan", "rel_type": "crush", "stage": "crush",
                                      "background": "Met at a friend's party two weeks ago", "goal": "see if he is serious"})
    assert r.status_code == 200, r.text
    p = r.json()
    created.append(p["id"])
    state["pid"] = p["id"]
    g = u.get(f"{API}/people/{p['id']}").json()
    assert g["nickname"] == "TEST_Ryan" and g["rel_type"] == "crush"


def test_exchange_six_part(u, state):
    pid = state["pid"]
    r = u.post(f"{API}/people/{pid}/exchanges", timeout=150, json={
        "conversation": [{"speaker": "me", "text": "Had fun last night!"}, {"speaker": "them", "text": "haha yeah. busy week tho"}],
        "question": "Is he losing interest?", "context_stage": "crush", "context_goal": "read him"})
    assert r.status_code == 200, r.text
    ex = r.json()
    for k in SIX:
        assert ex["response"].get(k), k
    assert ex["response"].get("confidence") in ("low", "medium", "high")
    assert isinstance(ex["proposed_memories"], list) and ex["proposed_memories"]
    state["ex"] = ex


def test_approve_memories_and_list(u, state):
    ex = state.get("ex") or pytest.skip("no exchange")
    mems = [{"kind": "event", "text": "TEST_ He said he had a busy week"}, {"kind": "interpretation", "text": "TEST_ Possibly pulling back"}]
    r = u.post(f"{API}/exchanges/{ex['id']}/memories", json={"memories": mems})
    assert r.status_code == 200 and r.json()["saved"] == 2
    lst = u.get(f"{API}/people/{state['pid']}/memories").json()
    texts = [m["text"] for m in lst]
    assert "TEST_ He said he had a busy week" in texts
    ev = next(m for m in lst if m["text"] == "TEST_ He said he had a busy week")
    state["event_mid"] = ev["id"]


def test_delete_memory_cascades_derived(u, state):
    mid = state.get("event_mid") or pytest.skip("no memory")
    r = u.delete(f"{API}/memories/{mid}")
    assert r.status_code == 200 and r.json()["derived_removed"] == 1
    lst = u.get(f"{API}/people/{state['pid']}/memories").json()
    assert not any(m["text"].startswith("TEST_") for m in lst)
    assert u.delete(f"{API}/memories/{mid}").status_code == 404


def test_outcome(u, state):
    ex = state.get("ex") or pytest.skip("no exchange")
    r = u.post(f"{API}/exchanges/{ex['id']}/outcome", json={"text": "He asked me out for Friday."})
    assert r.status_code == 200
    exs = u.get(f"{API}/people/{state['pid']}/exchanges").json()
    assert exs[0]["outcome"] == "He asked me out for Friday."
    assert u.post(f"{API}/exchanges/{ex['id']}/outcome", json={"text": ""}).status_code == 422


def test_question_only_exchange_and_empty_422(u, state):
    pid = state["pid"]
    assert u.post(f"{API}/people/{pid}/exchanges", json={"conversation": [], "question": " "}).status_code == 422


def test_prep(u, state):
    r = u.post(f"{API}/people/{state['pid']}/preps", timeout=150, json={"situation": "First proper date on Friday"})
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan.get("opening_line") and plan.get("exit_line") and 3 <= len(plan.get("reactions", [])) <= 4
    assert any(x["situation"] == "First proper date on Friday" for x in u.get(f"{API}/people/{state['pid']}/preps").json())


def test_notifications_and_read_all(u):
    r = u.get(f"{API}/notifications")
    assert r.status_code == 200 and "items" in r.json() and "unread" in r.json()
    assert u.post(f"{API}/notifications/read-all").json()["ok"] is True
    assert u.get(f"{API}/notifications").json()["unread"] == 0


def test_free_limit_402_on_4th(u, created):
    n = len(u.get(f"{API}/people").json()["people"])
    for i in range(3 - n):
        r = u.post(f"{API}/people", json={"nickname": f"TEST_F{i}", "rel_type": "friend", "stage": "established", "background": "a friend"})
        assert r.status_code == 200, r.text
        created.append(r.json()["id"])
    r = u.post(f"{API}/people", json={"nickname": "TEST_4th", "rel_type": "friend", "stage": "established", "background": "a friend"})
    assert r.status_code == 402


def test_report_built(u, state):
    for _ in range(18):
        p = u.get(f"{API}/people/{state['pid']}").json()
        if p.get("report_status") in ("ready", "error"):
            break
        time.sleep(5)
    assert p["report_status"] == "ready", p["report_status"]
    assert len(p["report"]["sections"]) == 9


# Admin RBAC
def test_admin_rbac():
    m, a = _sess(MEMBER), _sess(ADMIN)
    for path in ("/admin/archetypes", "/admin/figures", "/admin/settings", "/admin/metrics", "/users"):
        assert m.get(f"{API}{path}").status_code == 403, path
        assert a.get(f"{API}{path}").status_code == 200, path
    assert a.get(f"{API}/me").json()["role"] == "admin"
