# Iteration 4: quiz/about, onboarding auto-advance, rel types / no limit, overview, based_on, admin quiz/announce
import json
import os
import subprocess
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip()
BASE_URL = BASE_URL.rstrip("/")
API = f"{BASE_URL}/api"
FRESH_EMAIL = os.environ.get("FRESH_EMAIL", f"tester+fresh{uuid.uuid4().hex[:6]}_clerk_test@example.com")


def token_for(email):
    out = subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True)
    return json.loads(out)["token"]


def client(email):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token_for(email)}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def fresh():
    return client(FRESH_EMAIL)


@pytest.fixture(scope="module")
def member():
    return client("tester+clerk_test@example.com")


@pytest.fixture(scope="module")
def admin():
    return client("tester+admin_clerk_test@example.com")


@pytest.fixture(scope="module")
def created(member, fresh):
    ids = []
    yield ids
    for sess, pid in ids:
        sess.delete(f"{API}/people/{pid}")


# ---- quiz ----
def test_quiz_shape(fresh):
    r = fresh.get(f"{API}/quiz")
    assert r.status_code == 200
    d = r.json()
    assert len(d["axes"]) == 5 and len(d["short"]) == 8 and len(d["extended"]) == 20
    assert all(len(q["options"]) >= 2 for q in d["short"] + d["extended"])


def test_quiz_requires_auth():
    assert requests.get(f"{API}/quiz").status_code == 401


# ---- onboarding flow for a fresh user ----
class TestOnboardingFlow:
    def test_01_fresh_me(self, fresh):
        r = fresh.get(f"{API}/me")
        assert r.status_code == 200
        d = r.json()
        print("fresh me", d)
        assert (d.get("onboarding_step") or 1) == 1 and not d.get("onboarded")

    def test_02_about_save_and_validation(self, fresh):
        answers = {f"s{i}": {"choice": i % 4, "note": ""} for i in range(1, 9)}
        answers["e1"] = {"choice": 1, "note": "  TEST_note  "}
        answers["bogus"] = {"choice": 0}
        answers["e2"] = {"choice": 9}  # out of range
        r = fresh.put(f"{API}/me/about", json={"answers": answers, "describe": "TEST_ she is funny, loyal and a bit stubborn"})
        assert r.status_code == 200, r.text
        d = r.json()
        s = d["summary"]
        assert s["short_done"] == 8 and s["extended_done"] == 1
        assert len(s["tags"]) == 8 and isinstance(s["score"], int)
        assert s["score"] == round(50 + 20 + 1 / 20 * 30)
        assert "bogus" not in d["about"]["answers"] and "e2" not in d["about"]["answers"]
        assert d["about"]["answers"]["e1"]["note"] == "TEST_note"
        # null deletes
        r = fresh.put(f"{API}/me/about", json={"answers": {"e1": None}})
        assert r.status_code == 200
        assert r.json()["summary"]["extended_done"] == 0
        assert r.json()["about"]["describe"].startswith("TEST_")
        g = fresh.get(f"{API}/me/about").json()
        assert "e1" not in g["about"]["answers"] and g["summary"]["short_done"] == 8
        assert fresh.get(f"{API}/me").json()["onboarding_step"] == 2

    def test_02b_about_bad_types(self, fresh):
        r = fresh.put(f"{API}/me/about", json={"answers": {"s1": {"choice": "abc"}}})
        assert r.status_code == 422
        r = fresh.put(f"{API}/me/about", json={"answers": {"s1": {"choice": -1}}})
        assert r.status_code == 200 and r.json()["about"]["answers"]["s1"]["choice"] != -1

    def test_03_person_moves_step3(self, fresh, created):
        r = fresh.post(f"{API}/people", json={"nickname": "TEST_Sam", "rel_type": "situationship", "stage": "early", "background": "met at a party"})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        created.append((fresh, pid))
        me = fresh.get(f"{API}/me").json()
        assert me["onboarding_step"] == 3 and me["guide_person_id"] == pid
        TestOnboardingFlow.pid = pid

    def test_04_exchange_moves_step4(self, fresh):
        pid = TestOnboardingFlow.pid
        r = fresh.post(f"{API}/people/{pid}/exchanges", json={"conversation": [
            {"speaker": "them", "text": "hey sorry been busy, drinks friday?"}, {"speaker": "me", "text": "sure, where?"}],
            "question": "Is he keen?"}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d.get("based_on"), list) and d["based_on"]
        print("ex based_on", d["based_on"])
        assert any("quiz" in b for b in d["based_on"])
        assert fresh.get(f"{API}/me").json()["onboarding_step"] == 4

    def test_05_report_onboarded(self, fresh):
        pid = TestOnboardingFlow.pid
        time.sleep(3)
        r = fresh.post(f"{API}/people/{pid}/report/refresh", timeout=180)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["report_status"] == "ready", d.get("report_status")
        rep = d["report"]
        assert isinstance(rep.get("based_on"), list) and rep["based_on"]
        for t in rep.get("traits") or []:
            assert t["confidence"] in ("low", "medium", "high")
        print("traits", rep.get("traits"))
        me = fresh.get(f"{API}/me").json()
        assert me["onboarded"] is True

    def test_06_prep_based_on(self, fresh):
        pid = TestOnboardingFlow.pid
        r = fresh.post(f"{API}/people/{pid}/preps", json={"situation": "TEST_ asking him what we are"}, timeout=120)
        assert r.status_code == 200, r.text
        assert isinstance(r.json().get("based_on"), list) and r.json()["based_on"]

    def test_07_overview(self, fresh):
        r = fresh.get(f"{API}/overview")
        assert r.status_code == 200
        d = r.json()
        for k in ("groups", "feed", "activity", "checkins", "unreviewed", "unread", "about", "radar"):
            assert k in d, k
        assert set(d["groups"]) == {"romantic", "family", "friends", "work", "other"}
        assert d["groups"]["romantic"]["count"] >= 1
        assert len(d["activity"]) == 30 and sum(a["count"] for a in d["activity"]) >= 2
        assert len(d["radar"]) == 5 and d["about"]["short_done"] == 8
        assert any(f["type"] == "person" for f in d["feed"])


# ---- rel types / no limit ----
class TestPeopleTypes:
    @pytest.mark.parametrize("rt", ["situationship", "husband", "fwb", "long_distance", "dad", "best_friend", "client"])
    def test_new_rel_types(self, member, created, rt):
        r = member.post(f"{API}/people", json={"nickname": f"TEST_{rt}", "rel_type": rt, "stage": "established", "background": "x"})
        assert r.status_code == 200, r.text
        assert r.json()["rel_type"] == rt
        created.append((member, r.json()["id"]))

    def test_other_with_label(self, member, created):
        r = member.post(f"{API}/people", json={"nickname": "TEST_other", "rel_type": "other", "rel_label": "neighbour", "stage": "established", "background": "x"})
        assert r.status_code == 200
        created.append((member, r.json()["id"]))
        g = member.get(f"{API}/people/{r.json()['id']}").json()
        assert g["rel_label"] == "neighbour"

    def test_unknown_rel_422(self, member):
        r = member.post(f"{API}/people", json={"nickname": "TEST_bad", "rel_type": "alien", "stage": "early", "background": "x"})
        assert r.status_code == 422

    def test_no_limit(self, member):
        r = member.get(f"{API}/people")
        n = len(r.json()["people"])
        print("member people count", n, "limit", r.json()["limit"])
        assert n >= 4  # created above, no 402


# ---- admin ----
class TestAdmin:
    def test_metrics(self, admin):
        r = admin.get(f"{API}/admin/metrics")
        assert r.status_code == 200
        d = r.json()
        assert {"short", "extended", "users"} <= set(d["quiz"])
        assert isinstance(d["signups_by_day"], list) and isinstance(d["people_by_group"], dict)

    def test_settings_quiz_validation(self, admin):
        r = admin.put(f"{API}/admin/settings", json={"quiz": {"short": [{"id": "x", "q": "q", "axis": "Nope", "options": []}], "extended": []}})
        assert r.status_code == 422
        r = admin.put(f"{API}/admin/settings", json={"quiz": {"short": "bad"}})
        assert r.status_code == 422

    def test_settings_quiz_roundtrip(self, admin):
        cur = admin.get(f"{API}/admin/settings").json()["quiz"]
        r = admin.put(f"{API}/admin/settings", json={"quiz": cur})
        assert r.status_code == 200
        assert len(r.json()["quiz"]["short"]) == len(cur["short"])

    def test_announce(self, admin, member):
        title = f"TEST_announce {uuid.uuid4().hex[:5]}"
        r = admin.post(f"{API}/admin/announce", json={"title": title, "body": "TEST_ body", "link": "/app"})
        assert r.status_code == 200
        lst = admin.get(f"{API}/admin/announcements").json()
        assert any(a["title"] == title for a in lst)
        notes = member.get(f"{API}/notifications").json()
        items = notes.get("items", notes) if isinstance(notes, dict) else notes
        assert title in json.dumps(items)
        assert admin.post(f"{API}/admin/announce", json={"title": ""}).status_code == 422

    def test_non_admin_403(self, member):
        for m, p in (("get", "/admin/metrics"), ("get", "/admin/announcements"), ("post", "/admin/announce"), ("put", "/admin/settings")):
            r = getattr(member, m)(f"{API}{p}", json={"title": "x"} if m != "get" else None)
            assert r.status_code == 403, (p, r.status_code)
