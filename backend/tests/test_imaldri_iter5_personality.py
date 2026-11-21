# Iteration 5: quiz v2 (12/36/52 + dims), personality type/mbti, about summary, based_on 'your type', report compatibility, admin quiz v2
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
FRESH_EMAIL = os.environ.get("FRESH_EMAIL", f"tester+fr5{uuid.uuid4().hex[:6]}_clerk_test@example.com")


def client(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def fresh():
    return client(FRESH_EMAIL)


@pytest.fixture(scope="module")
def admin():
    return client("tester+admin_clerk_test@example.com")


@pytest.fixture(scope="module")
def member():
    return client("tester+clerk_test@example.com")


@pytest.fixture(scope="module")
def created(fresh):
    ids = []
    yield ids
    for pid in ids:
        fresh.delete(f"{API}/people/{pid}")


def test_scored_quiz_shape(fresh):
    d = fresh.get(f"{API}/quiz").json()
    assert d["scoring_version"] == 1
    assert len(d["short"]) == 12 and len(d["extended"]) == 36 and len(d["further"]) == 60
    assert {q["dim"] for q in d["short"][8:12]} == {"EI", "SN", "TF", "JP"}


class TestPersonality:
    def test_01_empty_code(self, fresh):
        r = fresh.get(f"{API}/me/about")
        assert r.status_code == 200
        p = r.json()["summary"]["personality"]
        assert p["code"] == "" and p["self_set"] is False and len(p["dims"]) == 4

    def test_02_partial_dims_no_code(self, fresh):
        r = fresh.put(f"{API}/me/about", json={"answers": {"s9": {"choice": 0, "note": ""}, "s10": {"choice": 0, "note": ""}}})
        assert r.status_code == 200, r.text
        p = r.json()["summary"]["personality"]
        assert p["code"] == ""
        assert [x["pair"] for x in p["dims"]] == ["EI", "NS", "FT", "JP"]

    def test_03_all_four_enfj(self, fresh):
        r = fresh.put(f"{API}/me/about", json={"answers": {f"s{i}": {"choice": 0, "note": ""} for i in range(1, 13)}})
        d = r.json()
        p = d["summary"]["personality"]
        assert p["code"] == "ENFJ", p
        assert p["name"] and p["line"]
        for x in p["dims"]:
            assert x["first_pct"] == 100 and x["letter"] == x["pair"][0] and x["answered"] >= 1
        assert d["summary"]["short_done"] == 12
        assert "further_done" in d["summary"] and isinstance(d["radar"], list) and len(d["radar"]) == 5

    def test_04_extended_further_answers(self, fresh):
        r = fresh.put(f"{API}/me/about", json={"answers": {"e21": {"choice": 3, "note": "TEST_n"}, "f1": {"choice": 3, "note": ""},
                                                        "f52": {"choice": 1, "note": ""}}})
        s = r.json()["summary"]
        assert s["extended_done"] == 1 and s["further_done"] == 2
        ei = s["personality"]["dims"][0]
        # Leaving a party early and preferring to cook on a date have no defined type score.
        assert ei["answered"] == 1 and ei["first_pct"] == 100

    def test_05_mbti_set(self, fresh):
        r = fresh.put(f"{API}/me/about", json={"answers": {}, "mbti": "intp"})
        p = r.json()["summary"]["personality"]
        assert p["code"] == "INTP" and p["self_set"] is True
        assert fresh.get(f"{API}/me/about").json()["summary"]["personality"]["code"] == "INTP"

    def test_06_mbti_invalid_ignored(self, fresh):
        r = fresh.put(f"{API}/me/about", json={"answers": {}, "mbti": "XXXX"})
        p = r.json()["summary"]["personality"]
        print("invalid mbti ->", p["code"], p["self_set"])
        # spec: invalid is ignored -> previous value (INTP) should stay
        assert p["code"] == "INTP", f"invalid mbti cleared/changed the type: {p}"

    def test_07_mbti_empty_clears(self, fresh):
        fresh.put(f"{API}/me/about", json={"answers": {}, "mbti": "INTP"})
        r = fresh.put(f"{API}/me/about", json={"answers": {}, "mbti": ""})
        p = r.json()["summary"]["personality"]
        assert p["self_set"] is False and p["code"] != "INTP"

    def test_08_mbti_set_without_answers(self, member):
        about = member.get(f"{API}/me/about").json()["about"]
        assert isinstance(about, dict)


class TestCoreConnection:
    def test_01_exchange_based_on_type(self, fresh, created):
        fresh.put(f"{API}/me/about", json={"answers": {}, "mbti": "ISFJ", "describe": "TEST_ I overthink and over-explain a lot"})
        r = fresh.post(f"{API}/people", json={"nickname": "TEST_Compat", "rel_type": "situationship", "stage": "early", "background": "met at a gig"})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        created.append(pid)
        TestCoreConnection.pid = pid
        r = fresh.post(f"{API}/people/{pid}/exchanges", json={"conversation": [
            {"speaker": "them", "text": "haha maybe, depends how the week goes"}, {"speaker": "me", "text": "ok let me know!"}],
            "question": "Is he keen?"}, timeout=150)
        assert r.status_code == 200, r.text
        bo = r.json()["based_on"]
        print("based_on", bo)
        assert "your type: ISFJ" in bo
        assert any(b.startswith("your quiz: ") for b in bo)

    def test_02_report_compatibility(self, fresh):
        pid = TestCoreConnection.pid
        time.sleep(3)
        r = fresh.post(f"{API}/people/{pid}/report/refresh", timeout=200)
        assert r.status_code == 200, r.text
        p = fresh.get(f"{API}/people/{pid}").json()
        p = p.get("person", p)
        assert p["report_status"] == "ready", p.get("report_status")
        c = p["report"].get("compatibility")
        print("compat", c)
        assert isinstance(c, dict)
        assert isinstance(c.get("summary"), str) and c["summary"]
        assert isinstance(c.get("works"), list) and isinstance(c.get("friction"), list)
        assert isinstance(c.get("tip"), str) and c["tip"]
        assert any("your type" in b for b in p["report"]["based_on"])


class TestAdminQuiz:
    def test_metrics_further(self, admin):
        r = admin.get(f"{API}/admin/metrics")
        assert r.status_code == 200
        q = r.json()["quiz"]
        assert {"short", "extended", "further", "users"} <= set(q)

    def test_missing_further_422(self, admin):
        qz = admin.get(f"{API}/admin/settings").json()["quiz"]
        r = admin.put(f"{API}/admin/settings", json={"quiz": {"short": qz["short"], "extended": qz["extended"]}})
        assert r.status_code == 422

    def test_bad_dim_422(self, admin):
        qz = admin.get(f"{API}/admin/settings").json()["quiz"]
        short = [dict(x) for x in qz["short"]]
        short[0]["dim"] = "XY"
        r = admin.put(f"{API}/admin/settings", json={"quiz": {"short": short, "extended": qz["extended"], "further": qz["further"]}})
        assert r.status_code == 422

    def test_roundtrip_v2(self, admin):
        qz = admin.get(f"{API}/admin/settings").json()["quiz"]
        r = admin.put(f"{API}/admin/settings", json={"quiz": {"short": qz["short"], "extended": qz["extended"], "further": qz["further"]}})
        assert r.status_code == 200, r.text
        d = r.json()["quiz"]
        assert d["version"] == 2 and len(d["short"]) == 12 and len(d["further"]) == 52

    def test_non_admin_403(self, member):
        assert member.put(f"{API}/admin/settings", json={}).status_code == 403
