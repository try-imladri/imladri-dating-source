"""imaldri API regression tests (public content, auth, people CRUD, isolation, AI flows, admin RBAC)."""
import json
import os
import subprocess
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    for line in open("/app/frontend/.env"):
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
API = f"{BASE_URL}/api"
MEMBER = "tester+clerk_test@example.com"
MEMBER2 = "tester2+clerk_test@example.com"
ADMIN = "tester+admin_clerk_test@example.com"


def _token(email):
    out = subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True)
    return json.loads(out)["token"]


@pytest.fixture(scope="module")
def member():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_token(MEMBER)}"
    return s


@pytest.fixture(scope="module")
def member2():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_token(MEMBER2)}"
    return s


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {_token(ADMIN)}"
    return s


@pytest.fixture(scope="module")
def created(member):
    ids = []
    yield ids
    for pid in ids:
        member.delete(f"{API}/people/{pid}")


# ---- public content ----
class TestPublic:
    def test_archetypes_47_unlocked(self):
        d = requests.get(f"{API}/public/archetypes").json()
        assert len(d) == 47
        assert all(x["unlocked"] for x in d)

    def test_love_bomber_characters_threads(self):
        d = requests.get(f"{API}/public/archetypes/love-bomber").json()
        sec = d["sections"]
        assert len(sec["characters"]) >= 1
        assert all(c.get("image_url") for c in sec["characters"])
        assert len(sec["threads"]) >= 4

    def test_all_archetypes_have_threads_and_characters(self):
        lst = requests.get(f"{API}/public/archetypes").json()
        bad = []
        for a in lst:
            d = requests.get(f"{API}/public/archetypes/{a['slug']}").json()
            s = d.get("sections") or {}
            if len(s.get("threads") or []) < 4 or not s.get("characters"):
                bad.append(a["slug"])
        assert not bad, f"archetypes missing threads/characters: {bad}"

    @pytest.mark.parametrize("slug", ["elon-musk", "drake", "john-mayer", "leonardo-dicaprio", "pete-davidson", "travis-kelce"])
    def test_figures_photos(self, slug):
        d = requests.get(f"{API}/public/figures/{slug}").json()
        assert d["photo_url"]
        r = requests.get(BASE_URL + d["photo_url"])
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
        assert len(r.content) > 5000

    # sections schema v2 + media serving
    def test_love_bomber_v2_and_media(self):
        d = requests.get(f"{API}/public/archetypes/love-bomber").json()
        assert d["sections"].get("v") == 2
        img = d["sections"]["characters"][0]["image_url"]
        url = img if img.startswith("http") else BASE_URL + img
        r = requests.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/")

    def test_figures_list_has_portraits(self):
        d = requests.get(f"{API}/public/figures").json()
        assert d and all(f.get("photo_url") for f in d)
        assert len({f["slug"] for f in d}) == len(d)

    # figure detail sections
    def test_elon_sections(self):
        d = requests.get(f"{API}/public/figures/elon-musk").json()
        assert d["name"] and (d.get("sections") or d.get("matches"))

    def test_public_config(self):
        r = requests.get(f"{API}/public/config")
        assert r.status_code == 200 and isinstance(r.json(), dict)

    def test_media_404(self):
        assert requests.get(f"{API}/media/does-not-exist").status_code == 404

    def test_unknown_archetype_404(self):
        assert requests.get(f"{API}/public/archetypes/nope-x").status_code == 404

    def test_waitlist_invalid_email(self):
        assert requests.post(f"{API}/public/waitlist", json={"email": "bad"}).status_code == 422


# ---- auth ----
class TestAuth:
    def test_no_token_401(self):
        assert requests.get(f"{API}/me").status_code == 401
        assert requests.get(f"{API}/people").status_code == 401

    def test_me(self, member):
        r = member.get(f"{API}/me")
        assert r.status_code == 200
        assert r.json()["email"] == MEMBER and r.json()["role"] == "member"

    def test_member_admin_forbidden(self, member):
        for path in ("/admin/archetypes", "/admin/settings", "/admin/metrics", "/users"):
            assert member.get(f"{API}{path}").status_code == 403, path


# ---- people CRUD + isolation + limits ----
class TestPeople:
    def test_crud_and_isolation(self, member, member2, created):
        r = member.post(f"{API}/people", json={"nickname": "TEST_Mom", "rel_type": "mom", "stage": "established", "background": "my mother, we talk weekly"})
        if r.status_code == 402:
            pytest.skip("free limit reached")
        assert r.status_code == 200
        p = r.json()
        created.append(p["id"])
        assert p["meter"]["score"] >= 0
        assert member.get(f"{API}/people/{p['id']}").json()["nickname"] == "TEST_Mom"
        # isolation
        assert member2.get(f"{API}/people/{p['id']}").status_code == 404
        assert member2.delete(f"{API}/people/{p['id']}").status_code == 404
        # archetype tag forbidden for non-romantic
        assert member.patch(f"{API}/people/{p['id']}", json={"archetype_tag": 3}).status_code == 422
        # patch
        r = member.patch(f"{API}/people/{p['id']}", json={"goal": "fewer fights"})
        assert r.status_code == 200 and r.json()["goal"] == "fewer fights"
        # intake
        r = member.patch(f"{API}/people/{p['id']}/intake", json={"fields": {"conflict": "She goes silent and then guilt trips me for days."}})
        assert r.status_code == 200 and r.json()["meter"]["fields_filled"] >= 1
        mems = member.get(f"{API}/people/{p['id']}/memories").json()
        assert any(m["id"] == "intake:conflict" for m in mems)

    def test_free_limit_402(self, member, created):
        n = len(member.get(f"{API}/people").json()["people"])
        codes = []
        for i in range(max(0, 4 - n)):
            r = member.post(f"{API}/people", json={"nickname": f"TEST_L{i}", "rel_type": "friend", "stage": "established", "background": "friend"})
            codes.append(r.status_code)
            if r.status_code == 200:
                created.append(r.json()["id"])
        assert codes and codes[-1] == 402


# ---- AI flows ----
class TestAI:
    def test_exchange_memories_outcome_prep(self, member, created):
        people = member.get(f"{API}/people").json()["people"]
        tp = [p for p in people if p["nickname"].startswith("TEST_")]
        if not tp:
            pytest.skip("no TEST person")
        pid = tp[0]["id"]
        r = member.post(f"{API}/people/{pid}/exchanges", timeout=120, json={
            "conversation": [{"speaker": "me", "text": "Can we talk Sunday?"}, {"speaker": "them", "text": "Fine. If you have time for me."}],
            "question": "Is she upset?", "context_stage": "established", "context_goal": "calm"})
        assert r.status_code == 200, r.text
        ex = r.json()
        for k in ("what_they_are_doing", "what_it_means", "best_move", "what_to_say", "what_not_to_do", "confidence"):
            assert ex["response"].get(k), k
        assert isinstance(ex["proposed_memories"], list)
        mems = ex["proposed_memories"][:2] or [{"kind": "event", "text": "TEST event"}]
        r = member.post(f"{API}/exchanges/{ex['id']}/memories", json={"memories": mems})
        assert r.status_code == 200 and r.json()["saved"] >= 1
        r = member.post(f"{API}/exchanges/{ex['id']}/outcome", json={"text": "She called and was warm."})
        assert r.status_code == 200
        r = member.post(f"{API}/people/{pid}/preps", timeout=120, json={"situation": "Dinner on Sunday with her"})
        assert r.status_code == 200, r.text
        plan = r.json()["plan"]
        assert plan.get("opening_line") and len(plan.get("reactions", [])) >= 3
        assert all(set(x) == {"reaction", "how_to_handle"} and x["reaction"] and x["how_to_handle"]
                   for x in plan["reactions"])
        # report builds in background
        for _ in range(20):
            time.sleep(5)
            p = member.get(f"{API}/people/{pid}").json()
            if p.get("report_status") in ("ready", "error"):
                break
        assert p["report_status"] == "ready", p.get("report_status")
        assert len(p["report"]["sections"]) == 9


# ---- admin ----
class TestAdmin:
    def test_admin_endpoints(self, admin):
        assert len(admin.get(f"{API}/admin/archetypes").json()) == 47
        assert admin.get(f"{API}/admin/figures").status_code == 200
        m = admin.get(f"{API}/admin/metrics")
        assert m.status_code == 200 and "onboarding_steps" in m.json()
        u = admin.get(f"{API}/users")
        assert u.status_code == 200 and any(x["email"] == ADMIN for x in u.json()["users"])
        s = admin.get(f"{API}/admin/settings").json()
        r = admin.put(f"{API}/admin/settings", json={"people_limit": s["people_limit"]})
        assert r.status_code == 200 and r.json()["people_limit"] == s["people_limit"]

    def test_admin_archetype_roundtrip(self, admin):
        a = admin.get(f"{API}/admin/archetypes").json()[0]
        r = admin.put(f"{API}/admin/archetypes/{a['slug']}", json={"one_liner": a["one_liner"]})
        assert r.status_code == 200 and r.json()["one_liner"] == a["one_liner"]
