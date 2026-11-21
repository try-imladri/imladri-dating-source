# Iteration 7: demographics (me/about + person), type_match background task, my_type in /people, exchanges with demographics
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
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = f"tester+it7{uuid.uuid4().hex[:6]}_clerk_test@example.com"
EMAIL2 = f"tester+it7b{uuid.uuid4().hex[:6]}_clerk_test@example.com"
STATE = {}


def mk(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="module")
def s():
    return mk(EMAIL)


@pytest.fixture(scope="module")
def s2():
    return mk(EMAIL2)


GOOD = {"age": 27, "sex": "Female", "ethnicity": "Mixed", "education": "Bachelor's"}


class TestMeDemographics:
    def test_put_valid(self, s):
        r = s.put(f"{API}/me/about", json={"demographics": GOOD})
        assert r.status_code == 200, r.text
        assert r.json()["about"]["demographics"] == GOOD

    def test_get_returns(self, s):
        assert s.get(f"{API}/me/about").json()["about"]["demographics"] == GOOD

    @pytest.mark.parametrize("bad", [{"age": 5, "sex": "Robot"}, {"age": 200, "sex": "Female"}, {"age": "abc", "education": "PhD"}])
    def test_invalid_dropped(self, s, bad):
        r = s.put(f"{API}/me/about", json={"demographics": bad})
        assert r.status_code == 200, r.text
        d = r.json()["about"]["demographics"]
        assert "age" not in d
        assert d.get("sex") in (None, "Female")
        assert "education" not in d

    def test_restore(self, s):
        r = s.put(f"{API}/me/about", json={"demographics": GOOD})
        assert r.json()["about"]["demographics"] == GOOD


class TestPersonDemographicsAndTypeMatch:
    def test_create_person(self, s):
        r = s.post(f"{API}/people", json={"nickname": "TEST_Jay", "rel_type": "crush", "stage": "talking", "background": "TEST met at a bar"})
        assert r.status_code == 200, r.text
        STATE["pid"] = r.json()["id"]

    def test_patch_demo(self, s):
        r = s.patch(f"{API}/people/{STATE['pid']}/demographics", json={"age": 30, "sex": "Male", "ethnicity": "White", "education": "Master's"})
        assert r.status_code == 200, r.text
        assert r.json()["demographics"] == {"age": 30, "sex": "Male", "ethnicity": "White", "education": "Master's"}
        g = s.get(f"{API}/people/{STATE['pid']}").json()
        assert g["demographics"]["age"] == 30

    def test_patch_demo_invalid(self, s):
        r = s.patch(f"{API}/people/{STATE['pid']}/demographics", json={"age": 200, "sex": "Alien", "ethnicity": "White"})
        assert r.status_code == 200, r.text
        assert r.json()["demographics"] == {"ethnicity": "White"}
        r = s.patch(f"{API}/people/{STATE['pid']}/demographics", json={"age": 30, "sex": "Male", "ethnicity": "White", "education": "Master's"})
        assert r.status_code == 200

    def test_other_user_404(self, s2):
        r = s2.patch(f"{API}/people/{STATE['pid']}/demographics", json={"age": 30})
        assert r.status_code in (403, 404), r.text

    def test_exchange_with_demo(self, s):
        r = s.post(f"{API}/people/{STATE['pid']}/exchanges", json={"question": "TEST should I text him first?"}, timeout=120)
        assert r.status_code == 200, r.text[:500]

    def test_my_type_empty(self, s2):
        r = s2.get(f"{API}/people")
        assert r.status_code == 200
        assert r.json()["my_type"] == ""

    def test_type_match_generated(self, s):
        assert s.put(f"{API}/me/about", json={"mbti": "INFJ"}).status_code == 200
        r = s.patch(f"{API}/people/{STATE['pid']}", json={"archetype_tag": 12})
        assert r.status_code == 200, r.text
        r = s.get(f"{API}/people")
        assert r.json()["my_type"] == "INFJ"
        tm = None
        for _ in range(12):
            time.sleep(5)
            p = next(x for x in s.get(f"{API}/people").json()["people"] if x["id"] == STATE["pid"])
            tm = p.get("type_match")
            if tm and tm.get("line"):
                break
        assert tm and tm["code"] == "INFJ" and tm["tag"] == 12 and len(tm["line"]) > 5, tm
        print("LINE:", tm["line"])

    def test_change_mbti_nulls(self, s):
        assert s.put(f"{API}/me/about", json={"mbti": "ESTP"}).status_code == 200
        r = s.get(f"{API}/people").json()
        assert r["my_type"] == "ESTP"
        p = next(x for x in r["people"] if x["id"] == STATE["pid"])
        assert p.get("type_match") is None
        tm = None
        for _ in range(12):
            time.sleep(5)
            p = next(x for x in s.get(f"{API}/people").json()["people"] if x["id"] == STATE["pid"])
            tm = p.get("type_match")
            if tm and tm.get("line"):
                break
        assert tm and tm["code"] == "ESTP", tm

    def test_non_romantic_no_tm(self, s):
        r = s.post(f"{API}/people", json={"nickname": "TEST_Mom", "rel_type": "mom", "stage": "close", "background": "TEST mom"})
        assert r.status_code == 200
        pid = r.json()["id"]
        r = s.patch(f"{API}/people/{pid}/demographics", json={"age": 55, "sex": "Female"})
        assert r.status_code == 200 and r.json()["demographics"] == {"age": 55, "sex": "Female"}
