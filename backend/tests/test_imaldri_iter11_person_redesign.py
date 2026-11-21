# Iteration 11: APIs behind person-page redesign (header photo, archive, delete) + type-compare top 3
import json
import subprocess

import pytest
import requests

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = "tester+clerk_test@example.com"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
STATE = {}


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    r = sess.post(f"{API}/people", json={"nickname": "TEST_Redesign", "rel_type": "crush", "stage": "early", "background": "test"})
    assert r.status_code in (200, 201), r.text
    STATE["pid"] = r.json()["id"]
    yield sess
    sess.delete(f"{API}/people/{STATE['pid']}")


def _get(s):
    r = s.get(f"{API}/people/{STATE['pid']}")
    assert r.status_code == 200
    g = r.json()
    return g.get("person", g)


# Unauthenticated access
def test_requires_auth():
    assert requests.get(f"{API}/people").status_code == 401


# Header photo
def test_avatar_patch_persists(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": PNG})
    assert r.status_code == 200
    assert _get(s)["avatar"] == PNG
    lst = s.get(f"{API}/people").json()["people"]
    assert any(p["id"] == STATE["pid"] and p.get("avatar") == PNG for p in lst)


# Archive / restore
def test_archive_toggle(s):
    assert s.patch(f"{API}/people/{STATE['pid']}", json={"archived": True}).status_code == 200
    assert _get(s)["archived"] is True
    assert s.patch(f"{API}/people/{STATE['pid']}", json={"archived": False}).status_code == 200
    assert _get(s)["archived"] is False


# Delete -> 404
def test_delete_then_404(s):
    r = s.post(f"{API}/people", json={"nickname": "TEST_Del", "rel_type": "crush", "stage": "early", "background": "test"})
    pid = r.json()["id"]
    assert s.delete(f"{API}/people/{pid}").status_code in (200, 204)
    assert s.get(f"{API}/people/{pid}").status_code == 404


# Type compare: >= 3 items with fields used by the share card
def test_type_compare_items(s):
    r = s.get(f"{API}/me/type-compare", timeout=90)
    assert r.status_code == 200
    items = r.json().get("items", [])
    if not items:
        pytest.skip("member has no type set")
    assert len(items) >= 3
    for t in items[:3]:
        for k in ("number", "name", "line", "score", "slug"):
            assert k in t
    scores = [t["score"] for t in items]
    assert scores == sorted(scores, reverse=True)
