"""Iteration-3: first-time onboarding steps (personal -> person -> chat -> report), no people limit, overview, admin quiz validation."""
import json
import subprocess
import time
import uuid

import pytest
import requests

BASE_URL = ""
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"
MEMBER = "tester+clerk_test@example.com"
ADMIN = "tester+admin_clerk_test@example.com"
FRESH = f"tester+iter3{uuid.uuid4().hex[:6]}_clerk_test@example.com"


def _sess(email):
    tok = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))["token"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def fresh():
    s = _sess(FRESH)
    s.ids = []
    yield s
    for pid in s.ids:
        s.delete(f"{API}/people/{pid}")
    s.delete(f"{API}/me")


@pytest.fixture(scope="module")
def admin():
    return _sess(ADMIN)


# Onboarding: fresh user starts at step 1, not onboarded
def test_fresh_user_step1(fresh):
    me = fresh.get(f"{API}/me").json()
    assert me["onboarded"] in (False, None)
    assert (me["onboarding_step"] or 1) == 1


# Onboarding: saving personal answers advances to step 2
def test_personal_save_advances(fresh):
    quiz = fresh.get(f"{API}/quiz").json()
    assert len(quiz["short"]) == 8 and len(quiz["extended"]) == 20
    ans = {q["id"]: {"choice": 0} for q in quiz["short"]}
    r = fresh.put(f"{API}/me/about", json={"answers": ans, "describe": "TEST_ loyal and funny"})
    assert r.status_code == 200
    assert r.json()["summary"]["short_done"] == 8
    assert fresh.get(f"{API}/me").json()["onboarding_step"] == 2
    # coming back: extended answer with note
    r = fresh.put(f"{API}/me/about", json={"answers": {quiz["extended"][0]["id"]: {"choice": 1, "note": "TEST note"}}})
    a = fresh.get(f"{API}/me/about").json()
    assert a["about"]["answers"][quiz["extended"][0]["id"]]["note"] == "TEST note"
    assert a["about"]["answers"]["s1"]["choice"] == 0
    assert a["summary"]["extended_done"] == 1


# Onboarding: adding a person -> step 3 + guide_person_id; >3 people not blocked
def test_people_no_limit_and_step3(fresh):
    for i, rel in enumerate(["crush", "mom", "best_friend", "boss", "husband"]):
        r = fresh.post(f"{API}/people", json={"nickname": f"TEST_P{i}", "rel_type": rel, "stage": "early", "background": "TEST bg"})
        assert r.status_code == 200, r.text
        fresh.ids.append(r.json()["id"])
    me = fresh.get(f"{API}/me").json()
    assert me["onboarding_step"] == 3
    assert me["guide_person_id"] in fresh.ids
    ov = fresh.get(f"{API}/overview").json()
    assert ov["total"] == 5
    assert ov["groups"]["romantic"]["count"] == 2
    assert ov["groups"]["family"]["count"] == 1
    assert ov["groups"]["work"]["count"] == 1
    assert len(ov["activity"]) == 30
    assert any(f["type"] == "person" for f in ov["feed"])
    assert ov["radar"][0]["axis"] and len(ov["radar"]) == 5


# Onboarding: conversation -> step 4 + based_on; report -> onboarded
def test_conversation_then_report_onboards(fresh):
    pid = fresh.ids[0]
    r = fresh.post(f"{API}/people/{pid}/exchanges", json={"conversation": [
        {"speaker": "me", "text": "hey are we still on for friday?"},
        {"speaker": "them", "text": "maybe, i'll let you know"}], "question": "is he flaky?"}, timeout=120)
    assert r.status_code == 200, r.text
    ex = r.json()
    assert ex["based_on"]  # NOTE: current conversation is not listed in based_on (computed before insert) - reported
    assert any("your quiz" in b for b in ex["based_on"])
    for k in ("what_they_are_doing", "what_it_means", "best_move", "what_to_say", "what_not_to_do"):
        assert ex["response"].get(k)
    me = fresh.get(f"{API}/me").json()
    assert me["onboarding_step"] >= 4
    # wait for background report (or force)
    for _ in range(30):
        p = fresh.get(f"{API}/people/{pid}").json()
        if p.get("report_status") == "ready":
            break
        time.sleep(3)
    else:
        fresh.post(f"{API}/people/{pid}/report/refresh", timeout=120)
        p = fresh.get(f"{API}/people/{pid}").json()
    assert p["report"]["sections"]
    assert p["report"]["based_on"]
    me = fresh.get(f"{API}/me").json()
    assert me["onboarded"] is True


# Validation: invalid rel_type 422
def test_invalid_rel(fresh):
    r = fresh.post(f"{API}/people", json={"nickname": "TEST_x", "rel_type": "alien", "stage": "early", "background": "x"})
    assert r.status_code == 422


# Admin: settings quiz validation rejects bad axis
def test_admin_quiz_validation(admin):
    s = admin.get(f"{API}/admin/settings").json()
    bad = {"short": [{"id": "s1", "q": "x", "axis": "Nope", "options": [{"label": "a", "score": 1}, {"label": "b", "score": 2}]}],
           "extended": []}
    assert admin.put(f"{API}/admin/settings", json={"quiz": bad}).status_code == 422
    assert admin.get(f"{API}/admin/settings").json()["quiz"] == s["quiz"]


# RBAC: member cannot announce/settings/metrics
def test_member_admin_forbidden():
    m = _sess(MEMBER)
    assert m.post(f"{API}/admin/announce", json={"title": "TEST_x"}).status_code == 403
    assert m.put(f"{API}/admin/settings", json={}).status_code == 403
    assert m.get(f"{API}/admin/metrics").status_code == 403
    assert requests.get(f"{API}/overview").status_code == 401
