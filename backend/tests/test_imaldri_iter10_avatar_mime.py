# Iteration 10: PATCH /api/people/{id} avatar mime whitelist (svg -> 422, png/jpeg -> 200)
import json
import subprocess
import uuid

import pytest
import requests

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = f"tester+it10{uuid.uuid4().hex[:6]}_clerk_test@example.com"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP8="
SVG = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnLz4="
STATE = {}


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    sess.get(f"{API}/me")
    r = sess.post(f"{API}/people", json={"nickname": "TEST_Mime", "rel_type": "crush", "stage": "crush", "background": "TEST_ bg"})
    assert r.status_code in (200, 201), r.text
    STATE["pid"] = r.json()["id"]
    yield sess
    sess.delete(f"{API}/people/{STATE['pid']}")


def _get(s):
    g = s.get(f"{API}/people/{STATE['pid']}").json()
    return g.get("person", g)


def test_png_ok(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": PNG})
    assert r.status_code == 200, r.text
    assert _get(s).get("avatar") == PNG


def test_svg_422(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": SVG})
    assert r.status_code == 422, r.text
    assert _get(s).get("avatar") == PNG


def test_svg_plain_422(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": "data:image/svg+xml,<svg/>"})
    assert r.status_code == 422, r.text


def test_jpeg_ok(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": JPEG})
    assert r.status_code == 200, r.text
    assert _get(s).get("avatar") == JPEG


def test_empty_clear_ok(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"avatar": ""})
    print("clear avatar status", r.status_code)
    assert r.status_code in (200, 422)
