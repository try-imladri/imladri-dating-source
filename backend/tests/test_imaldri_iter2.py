"""Iteration-2 regression: quiz, About You, overview, expanded rel types, based_on, admin announce/settings/metrics."""
import json
import subprocess
import time

import pytest
import requests

BASE_URL = ""
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"
QA = "tester_iter2+clerk_test@example.com"
ADMIN = "tester+admin_clerk_test@example.com"


def _sess(email):
    tok = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))["token"]
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    return s


@pytest.fixture(scope="module")
def qa():
    s = _sess(QA)
    s.ids = []
    yield s
    for pid in s.ids:
        s.delete(f"{API}/people/{pid}")


@pytest.fixture(scope="module")
def admin():
    return _sess(ADMIN)


def _person(qa, rel="crush", name="TEST_I2", **kw):
    r = qa.post(f"{API}/people", json={"nickname": name, "rel_type": rel, "stage": "early", "background": "met at a party", **kw})
    assert r.status_code == 200, r.text
    qa.ids.append(r.json()["id"])
    return r.json()


# quiz
def test_quiz_shape(qa):
    r = qa.get(f"{API}/quiz")
    assert r.status_code == 200
    d = r.json()
    assert len(d["axes"]) == 5 and len(d["short"]) == 8 and len(d["extended"]) == 20


def test_quiz_requires_auth():
    assert requests.get(f"{API}/quiz").status_code in (401, 403)


# about you
def test_about_save_and_remove(qa):
    r = qa.put(f"{API}/me/about", json={"answers": {"s1": {"choice": 0}, "s2": {"choice": 3}, "e1": {"choice": 1, "note": "TEST note"}},
                                         "describe": "TEST_ I am a pretty direct person"})
    assert r.status_code == 200, r.text
    summ = r.json()["summary"]
    assert "direct communicator" in summ["tags"] and summ["short_done"] == 2 and summ["extended_done"] == 1
    assert summ["score"] > 0
    g = qa.get(f"{API}/me/about").json()
    assert g["about"]["answers"]["s1"]["choice"] == 0
    assert g["summary"]["describe"].startswith("TEST_")
    r2 = qa.put(f"{API}/me/about", json={"answers": {"s2": None}})
    assert r2.status_code == 200
    g2 = qa.get(f"{API}/me/about").json()
    assert "s2" not in g2["about"]["answers"] and "s1" in g2["about"]["answers"]
    assert g2["summary"]["describe"].startswith("TEST_")  # describe untouched when omitted


def test_about_invalid_choice_ignored(qa):
    r = qa.put(f"{API}/me/about", json={"answers": {"s3": {"choice": 99}, "zzz": {"choice": 0}}})
    assert r.status_code == 200
    assert "s3" not in r.json()["about"]["answers"] and "zzz" not in r.json()["about"]["answers"]


# overview
def test_overview_shape(qa):
    _person(qa, "dad", "TEST_Dad")
    r = qa.get(f"{API}/overview")
    assert r.status_code == 200
    d = r.json()
    for k in ("groups", "total", "feed", "activity", "checkins", "unreviewed", "unread", "about", "radar"):
        assert k in d
    assert set(d["groups"]) == {"romantic", "family", "friends", "work", "other"}
    assert len(d["activity"]) == 30
    assert d["groups"]["family"]["count"] >= 1
    assert len(d["radar"]) == 5
    assert any(f["nickname"] == "TEST_Dad" for f in d["feed"])


# people types
@pytest.mark.parametrize("rel", ["situationship", "dad", "best_friend", "client"])
def test_new_rel_types(qa, rel):
    p = _person(qa, rel, f"TEST_{rel}"[:40])
    assert p["rel_type"] == rel
    assert qa.delete(f"{API}/people/{p['id']}").status_code == 200
    assert qa.get(f"{API}/people/{p['id']}").status_code == 404


def test_other_with_label(qa):
    p = _person(qa, "other", "TEST_Other", rel_label="Neighbour")
    assert p["rel_label"] == "Neighbour"
    assert qa.get(f"{API}/people/{p['id']}").json()["rel_label"] == "Neighbour"
    qa.delete(f"{API}/people/{p['id']}")


def test_unknown_rel_type(qa):
    r = qa.post(f"{API}/people", json={"nickname": "TEST_x", "rel_type": "alien", "stage": "early", "background": "x"})
    assert r.status_code == 422


# based_on (AI)
def test_exchange_prep_report_based_on(qa):
    p = _person(qa, "situationship", "TEST_Sit")
    r = qa.post(f"{API}/people/{p['id']}/exchanges", timeout=120, json={
        "conversation": [{"speaker": "them", "text": "hey sorry been busy this week"}, {"speaker": "me", "text": "no worries, are we still on for friday?"},
                         {"speaker": "them", "text": "maybe, ill let you know"}], "question": "Is he losing interest?"})
    assert r.status_code == 200, r.text
    ex = r.json()
    assert isinstance(ex["based_on"], list) and any(b.startswith("your quiz:") for b in ex["based_on"]), ex["based_on"]
    assert ex["response"].get("best_move")
    pr = qa.post(f"{API}/people/{p['id']}/preps", timeout=120, json={"situation": "Seeing him Friday for drinks"})
    assert pr.status_code == 200, pr.text
    assert any(b.startswith("your quiz:") for b in pr.json()["based_on"])
    rep = None
    for _ in range(30):
        time.sleep(3)
        pp = qa.get(f"{API}/people/{p['id']}").json()
        if pp.get("report_status") in ("ready", "error") and pp.get("report"):
            rep = pp["report"]
            break
        if pp.get("report_status") == "error":
            break
    assert rep, f"report not ready: {pp.get('report_status')}"
    assert isinstance(rep["based_on"], list) and len(rep["based_on"]) > 0
    assert "traits" in rep and isinstance(rep["traits"], list)


# admin
def test_announce_member_forbidden(qa):
    assert qa.post(f"{API}/admin/announce", json={"title": "TEST_x"}).status_code == 403


def test_announce_flow(admin, qa):
    r = admin.post(f"{API}/admin/announce", json={"title": "TEST_ announcement iter2", "body": "hello", "link": "/app"})
    assert r.status_code == 200, r.text
    lst = admin.get(f"{API}/admin/announcements").json()
    assert any(a["title"] == "TEST_ announcement iter2" for a in lst)
    n = qa.get(f"{API}/notifications").json()
    assert any(i["title"] == "TEST_ announcement iter2" and i["type"] == "announcement" for i in n["items"])


def test_settings_invalid_quiz(admin):
    r = admin.put(f"{API}/admin/settings", json={"quiz": {"short": [{"id": "x", "q": "?", "axis": "Bogus", "options": []}], "extended": []}})
    assert r.status_code == 422
    assert len(admin.get(f"{API}/quiz").json()["short"]) == 8


def test_metrics(admin, qa):
    d = admin.get(f"{API}/admin/metrics").json()
    assert set(d["onboarding_steps"]) == {"1", "2", "3", "4", "5"}
    assert {"short", "extended", "users"} <= set(d["quiz"])
    assert isinstance(d["signups_by_day"], list) and isinstance(d["people_by_group"], dict)
    assert qa.get(f"{API}/admin/metrics").status_code == 403
