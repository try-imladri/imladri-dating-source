# Iteration 6: retest of PUT /api/me/about mbti handling (invalid ignored, '' clears, valid sets) and self-picked dims
import json
import os
import subprocess
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = f"tester+fr6{uuid.uuid4().hex[:6]}_clerk_test@example.com"


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    return sess


def put(s, body):
    r = s.put(f"{API}/me/about", json=body)
    assert r.status_code == 200, r.text
    return r.json()["summary"]["personality"]


def test_unauth_401():
    assert requests.get(f"{API}/me/about").status_code == 401


class TestMbti:
    def test_01_quiz_enfj(self, s):
        p = put(s, {"answers": {f"s{i}": {"choice": 0, "note": ""} for i in range(1, 13)}})
        assert p["code"] == "ENFJ" and p["self_set"] is False

    def test_02_valid_sets(self, s):
        p = put(s, {"mbti": "istp"})
        assert p["code"] == "ISTP" and p["self_set"] is True

    def test_03_dims_match_picked(self, s):
        p = s.get(f"{API}/me/about").json()["summary"]["personality"]
        assert "".join(d["letter"] for d in p["dims"]) == "ISTP"
        assert all(d["first_pct"] is None for d in p["dims"])

    def test_04_invalid_keeps(self, s):
        p = put(s, {"mbti": "XXXX"})
        assert p["code"] == "ISTP" and p["self_set"] is True
        p2 = s.get(f"{API}/me/about").json()["summary"]["personality"]
        assert p2["code"] == "ISTP"

    def test_05_other_valid(self, s):
        p = put(s, {"mbti": "ENTP"})
        assert p["code"] == "ENTP"
        assert "".join(d["letter"] for d in p["dims"]) == "ENTP"
        assert all(d["first_pct"] is None for d in p["dims"])

    def test_06_empty_clears(self, s):
        p = put(s, {"mbti": ""})
        assert p["code"] == "ENFJ" and p["self_set"] is False
        assert [d["first_pct"] for d in p["dims"]] == [100, 100, 100, 100]

    def test_07_invalid_when_none_set(self, s):
        p = put(s, {"mbti": "XXXX"})
        assert p["code"] == "ENFJ" and p["self_set"] is False
