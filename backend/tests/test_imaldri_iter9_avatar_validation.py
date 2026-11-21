# Iteration 9: PATCH /api/people/{id} avatar validation (non data:image -> 422) + member overview demographics
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
EMAIL = f"tester+it9{uuid.uuid4().hex[:6]}_clerk_test@example.com"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
STATE = {}
RT = "crush"


def mk(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def s():
    sess = mk(EMAIL)
    sess.get(f"{API}/me")
    yield sess
    if STATE.get("pid"):
        sess.delete(f"{API}/people/{STATE['pid']}")


def _person(s):
    if "pid" not in STATE:
        r = s.post(f"{API}/people", json={"nickname": "TEST_Avatar", "rel_type": RT, "stage": "crush", "background": "TEST_ met at work"})
        assert r.status_code in (200, 201), r.text
        STATE["pid"] = r.json()["id"]
    return STATE["pid"]


class TestAvatar:
    @pytest.mark.parametrize("bad", ["https://evil.example.com/x.png", "javascript:alert(1)", "data:text/html;base64,PGgxPg==", "hello"])
    def test_bad_avatar_422(self, s, bad):
        pid = _person(s)
        r = s.patch(f"{API}/people/{pid}", json={"avatar": bad})
        assert r.status_code == 422, r.text
        g = s.get(f"{API}/people/{pid}").json()
        g = g.get("person", g)
        assert (g.get("avatar") or "") != bad

    def test_good_avatar_ok(self, s):
        pid = _person(s)
        r = s.patch(f"{API}/people/{pid}", json={"avatar": PNG})
        assert r.status_code == 200, r.text
        assert r.json().get("avatar") == PNG
        g = s.get(f"{API}/people/{pid}").json()
        g = g.get("person", g)
        assert g.get("avatar") == PNG

    def test_too_large_avatar_422(self, s):
        pid = _person(s)
        r = s.patch(f"{API}/people/{pid}", json={"avatar": "data:image/png;base64," + "A" * 300_001})
        assert r.status_code == 422


class TestMemberOverview:
    def test_demographics_present(self):
        m = mk("tester+clerk_test@example.com")
        r = m.get(f"{API}/overview")
        assert r.status_code == 200, r.text
        demo = r.json()["about"].get("demographics") or {}
        print("member demographics", demo)
        assert demo.get("age") or demo.get("sex")
