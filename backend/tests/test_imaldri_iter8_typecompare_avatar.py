# Iteration 8: /me/type-compare (ranked archetypes), person avatar PATCH/GET, overview about.demographics
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
EMAIL = f"tester+it8{uuid.uuid4().hex[:6]}_clerk_test@example.com"
STATE = {}
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def mk(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="module")
def s():
    return mk(EMAIL)


# type compare
class TestTypeCompare:
    def test_unauth(self):
        assert requests.get(f"{API}/me/type-compare").status_code in (401, 403)

    def test_no_type_empty(self, s):
        r = s.get(f"{API}/me/type-compare")
        assert r.status_code == 200, r.text
        assert r.json() == {"code": "", "items": []}

    def test_with_type(self, s):
        assert s.put(f"{API}/me/about", json={"mbti": "INFJ", "answers": {}}).status_code == 200
        t = time.time()
        r = s.get(f"{API}/me/type-compare", timeout=120)
        STATE["first"] = time.time() - t
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["code"] == "INFJ"
        arch = requests.get(f"{API}/public/archetypes").json()
        arch = arch.get("archetypes", arch) if isinstance(arch, dict) else arch
        STATE["n_arch"] = len(arch)
        items = d["items"]
        print("first call secs", STATE["first"], "items", len(items), "archetypes", len(arch))
        assert len(items) == len(arch)
        scores = [i["score"] for i in items]
        assert scores == sorted(scores, reverse=True)
        for i in items:
            assert set(i) >= {"number", "name", "slug", "score", "line"}
            assert 0 <= i["score"] <= 100 and isinstance(i["line"], str) and i["line"]
        assert len({i["number"] for i in items}) == len(items)
        STATE["items"] = items

    def test_cached(self, s):
        t = time.time()
        r = s.get(f"{API}/me/type-compare")
        assert time.time() - t < 5
        assert r.json()["items"] == STATE["items"]

    def test_type_change_recomputes(self, s):
        assert s.put(f"{API}/me/about", json={"mbti": "ESTP", "answers": {}}).status_code == 200
        r = s.get(f"{API}/me/type-compare", timeout=120)
        assert r.json()["code"] == "ESTP"


# person avatar
class TestPersonAvatar:
    def test_create_and_patch_avatar(self, s):
        r = s.post(f"{API}/people", json={"nickname": "TEST_Photo", "rel_type": "friend", "stage": "good", "background": "TEST bg"})
        if r.status_code != 200:
            r = s.post(f"{API}/people", json={"nickname": "TEST_Photo", "rel_type": "dating", "stage": "early", "background": "TEST bg"})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        STATE["pid"] = pid
        r = s.patch(f"{API}/people/{pid}", json={"avatar": PNG})
        assert r.status_code == 200, r.text
        assert r.json()["avatar"] == PNG

    def test_list_includes_avatar(self, s):
        ppl = s.get(f"{API}/people").json()["people"]
        p = next(p for p in ppl if p["id"] == STATE["pid"])
        assert p["avatar"] == PNG
        assert s.get(f"{API}/people/{STATE['pid']}").json()["avatar"] == PNG

    def test_too_large(self, s):
        r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": "data:image/png;base64," + "A" * 300_001})
        assert r.status_code == 422

    def test_clear_avatar(self, s):
        r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": ""})
        assert r.status_code == 200 and r.json()["avatar"] == ""

    def test_non_image_avatar(self, s):
        r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": "javascript:alert(1)"})
        print("non-image avatar status", r.status_code)
        STATE["non_image"] = r.status_code

    def test_other_user_cannot_patch(self):
        s2 = mk(f"tester+it8b{uuid.uuid4().hex[:6]}_clerk_test@example.com")
        assert s2.patch(f"{API}/people/{STATE['pid']}", json={"avatar": PNG}).status_code in (403, 404)

    def test_cleanup(self, s):
        assert s.delete(f"{API}/people/{STATE['pid']}").status_code in (200, 204)
        assert s.get(f"{API}/people/{STATE['pid']}").status_code == 404


# overview demographics
class TestOverviewDemo:
    def test_demographics(self, s):
        demo = {"age": 27, "sex": "Female", "ethnicity": "Mixed", "education": "Bachelor's"}
        assert s.put(f"{API}/me/about", json={"demographics": demo}).status_code == 200
        d = s.get(f"{API}/overview").json()
        assert d["about"]["demographics"] == demo
