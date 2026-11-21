# Iteration 12: person basics/intake/demographics persist and feed full_context ("Based on") for chat exchanges and prepare plans
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
STATE = {}


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    r = sess.post(f"{API}/people", json={"nickname": "TEST_Ctx", "rel_type": "crush", "stage": "early", "background": "met at gym"})
    assert r.status_code in (200, 201), r.text
    STATE["pid"] = r.json()["id"]
    yield sess
    sess.delete(f"{API}/people/{STATE['pid']}")


def _get(s):
    r = s.get(f"{API}/people/{STATE['pid']}")
    assert r.status_code == 200
    g = r.json()
    return g.get("person", g)


# Auth on exchanges/preps
def test_auth_required():
    assert requests.get(f"{API}/people/x/exchanges").status_code == 401
    assert requests.post(f"{API}/people/x/preps", json={"situation": "abc"}).status_code == 401


# Baseline: no goal/demographics -> Based on lacks those
def test_exchange_baseline_based_on(s):
    r = s.post(f"{API}/people/{STATE['pid']}/exchanges", json={"question": "TEST_ should I text first?"}, timeout=90)
    assert r.status_code == 200, r.text
    bo = r.json()["based_on"]
    assert "your goal" not in bo and "their details" not in bo
    assert bo[0].startswith("profile: crush")


# Basics patch persists
def test_patch_basics_persist(s):
    arch = requests.get(f"{API}/public/archetypes").json()
    unlocked = [a for a in arch if a.get("unlocked")]
    body = {"nickname": "TEST_CtxRenamed", "stage": "established", "goal": "find out if he is serious", "background": "met at the climbing gym"}
    if unlocked:
        body["archetype_tag"] = unlocked[0]["number"]
        STATE["arch"] = unlocked[0]["number"]
    r = s.patch(f"{API}/people/{STATE['pid']}", json=body)
    assert r.status_code == 200, r.text
    r = s.patch(f"{API}/people/{STATE['pid']}/demographics", json={"age": 29, "sex": "Male"})
    assert r.status_code == 200, r.text
    p = _get(s)
    assert p["nickname"] == "TEST_CtxRenamed"
    assert p["stage"] == "established"
    assert p["goal"] == "find out if he is serious"
    assert p["background"] == "met at the climbing gym"
    assert p["demographics"].get("age") == 29
    if unlocked:
        assert p["archetype_tag"] == STATE["arch"]


# Intake save persists
def test_intake_persist(s):
    r = s.patch(f"{API}/people/{STATE['pid']}/intake", json={"fields": {"conflict": "He goes quiet for a day after any disagreement", "talk_text": "Short texts, mostly memes"}})
    assert r.status_code == 200, r.text
    p = _get(s)
    assert p["intake"]["conflict"].startswith("He goes quiet")


# Exchange now reflects saved fields
def test_exchange_based_on_reflects_fields(s):
    r = s.post(f"{API}/people/{STATE['pid']}/exchanges", json={"question": "TEST_ is he losing interest?"}, timeout=90)
    assert r.status_code == 200, r.text
    bo = r.json()["based_on"]
    print(bo)
    assert any(b.startswith("profile: crush, established") or b.startswith("profile: ") and "established" in b for b in bo)
    assert "your goal" in bo
    assert "their details" in bo
    assert any("earlier advice" in b for b in bo)
    assert any(b.startswith("profile: ") and ("how you fight" in b.lower() or "," in b and "crush" not in b) for b in bo)
    if STATE.get("arch"):
        assert f"archetype #{STATE['arch']} (your tag)" in bo
    lst = s.get(f"{API}/people/{STATE['pid']}/exchanges").json()
    assert len(lst) == 2 and lst[-1]["question"] == "TEST_ is he losing interest?"


# Prepare plan reflects saved fields and persists
def test_prep_based_on_and_list(s):
    r = s.post(f"{API}/people/{STATE['pid']}/preps", json={"situation": "TEST_ dinner friday, want to ask where this is going"}, timeout=90)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "your goal" in d["based_on"] and "their details" in d["based_on"]
    assert d["plan"].get("opening_line")
    assert isinstance(d["plan"].get("reactions"), list)
    lst = s.get(f"{API}/people/{STATE['pid']}/preps").json()
    assert lst[0]["id"] == d["id"]


# Validation edge cases
def test_prep_too_short(s):
    assert s.post(f"{API}/people/{STATE['pid']}/preps", json={"situation": "ab"}).status_code == 422


def test_exchange_empty(s):
    assert s.post(f"{API}/people/{STATE['pid']}/exchanges", json={"question": "  "}).status_code == 422


def test_patch_empty_name_rejected(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"nickname": ""})
    ok = r.status_code == 422
    if not ok:
        s.patch(f"{API}/people/{STATE['pid']}", json={"nickname": "TEST_CtxRenamed"})
    assert ok, f"empty name accepted: {r.status_code}"


def test_patch_invalid_stage_rejected(s):
    r = s.patch(f"{API}/people/{STATE['pid']}", json={"stage": "bogus_stage"})
    ok = r.status_code == 422
    if not ok:
        s.patch(f"{API}/people/{STATE['pid']}", json={"stage": "established"})
    assert ok, f"invalid stage accepted: {r.status_code}"
