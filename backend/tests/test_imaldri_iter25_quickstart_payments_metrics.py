# Iteration 25: quickstart speaker check, free-tier limit, practice scenarios (tone, no likelihood), payments (Stripe), admin metrics
import json
import os
import subprocess
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
T = 180

load_dotenv("/app/backend/.env")
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _sess(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    me = s.get(f"{API}/me", timeout=T)
    assert me.status_code == 200
    s.uid = out["user_id"]
    return s


@pytest.fixture(scope="module")
def created():
    items = []
    yield items
    for s, pid in items:
        s.delete(f"{API}/people/{pid}", timeout=T)


@pytest.fixture(scope="module")
def fresh():
    return _sess(f"tester+qs{uuid.uuid4().hex[:6]}_clerk_test@example.com")


@pytest.fixture(scope="module")
def admin():
    return _sess("tester+admin_clerk_test@example.com")


# --- quickstart parse + create with confirmed lines
class TestQuickstart:
    def test_parse_returns_lines(self, fresh):
        r = fresh.post(f"{API}/quickstart/parse", json={"nickname": "TEST_Sam", "conversation_text": "Me: hey are we still on for friday?\nSam: idk maybe\nok cool\nwhatever works"}, timeout=T)
        assert r.status_code == 200, r.text
        lines = r.json()["lines"]
        assert len(lines) >= 3
        for l in lines:
            assert l["speaker"] in ("me", "them") and isinstance(l["sure"], bool) and l["text"]
        print("parsed:", lines)

    def test_invalid_rel_type_422(self, fresh):
        r = fresh.post(f"{API}/quickstart", json={"nickname": "TEST_x", "what_happened": "x", "rel_type": "alien"}, timeout=T)
        assert r.status_code == 422

    def test_quickstart_with_confirmed_lines(self, fresh, created):
        lines = [{"speaker": "them", "text": "hey are we still on for friday?"}, {"speaker": "me", "text": "idk maybe"}]
        r = fresh.post(f"{API}/quickstart", json={"nickname": "TEST_QsA", "what_happened": "He went vague about plans", "rel_type": "coworker",
                                                  "stage": "strained", "lines": lines}, timeout=T)
        assert r.status_code == 200, r.text
        d = r.json()
        created.append((fresh, d["person"]["id"]))
        assert d["person"]["rel_type"] == "coworker" and d["person"]["stage"] == "strained"
        ex = DB.exchanges.find_one({"id": d["exchange"]["id"]})
        assert [c["speaker"] for c in ex["conversation"]] == ["them", "me"]
        assert ex["has_conversation"] is True

    def test_quickstart_empty_lines_no_convo(self, fresh, created):
        r = fresh.post(f"{API}/quickstart", json={"nickname": "TEST_QsB", "what_happened": "My mom keeps calling at work", "rel_type": "mom",
                                                  "lines": [], "conversation_text": "Me: hi\nMom: hi"}, timeout=T)
        assert r.status_code == 200, r.text
        d = r.json()
        created.append((fresh, d["person"]["id"]))
        ex = DB.exchanges.find_one({"id": d["exchange"]["id"]})
        assert ex["conversation"] == [] and ex["has_conversation"] is False
        assert d["person"]["stage"] == "good"

    def test_quickstart_invalid_stage_accepted(self, fresh, created):
        # Code review probe: stage is not validated against rel_type in /quickstart
        r = fresh.post(f"{API}/quickstart", json={"nickname": "TEST_QsC", "what_happened": "x", "rel_type": "friend", "stage": "bogus", "lines": []}, timeout=T)
        if r.status_code == 200:
            created.append((fresh, r.json()["person"]["id"]))
        print("invalid stage status:", r.status_code, r.json().get("person", {}).get("stage") if r.status_code == 200 else r.text)


# --- free tier limit
class TestLimit:
    def test_free_limit_402(self, fresh, created):
        n = DB.people.count_documents({"user_id": fresh.uid, "archived": {"$ne": True}})
        assert n == 3, n
        r = fresh.post(f"{API}/people", json={"nickname": "TEST_4th", "rel_type": "friend", "stage": "good", "background": "x"}, timeout=T)
        assert r.status_code == 402 and "Go Unlimited" in r.json()["detail"]
        r = fresh.post(f"{API}/quickstart", json={"nickname": "TEST_4th", "what_happened": "x", "rel_type": "friend", "lines": []}, timeout=T)
        assert r.status_code == 402 and "Go Unlimited" in r.json()["detail"]

    def test_admin_not_limited(self, admin, created):
        r = admin.post(f"{API}/people", json={"nickname": "TEST_AdmLim", "rel_type": "friend", "stage": "good", "background": "x"}, timeout=T)
        assert r.status_code == 200, r.text
        created.append((admin, r.json()["id"]))


# --- practice likely: tone, no likelihood
class TestPractice:
    def test_likely_options_tone(self, admin, created):
        p = admin.post(f"{API}/people", json={"nickname": "TEST_Prac25", "rel_type": "partner", "stage": "established", "background": "He forgets plans"}, timeout=T).json()
        created.append((admin, p["id"]))
        x = admin.post(f"{API}/practice", json={"person_id": p["id"], "topic": "Forgotten dinner", "mode": "likely"}, timeout=T).json()
        r = admin.post(f"{API}/practice/{x['id']}/say", json={"text": "Hey, I felt hurt you forgot dinner"}, timeout=T)
        assert r.status_code == 200, r.text
        opts = r.json()["options"]
        assert len(opts) == 3
        for o in opts:
            assert "tone" in o and "likelihood" not in o
        print("tones:", [o["tone"] for o in opts])


# --- payments
class TestPayments:
    def test_upgrade_info(self, fresh):
        d = fresh.get(f"{API}/upgrade", timeout=T).json()
        assert d["price_label"].startswith("$") and "/month" in d["price_label"]
        assert d["tier"] == "free"

    def test_checkout_creates_tx(self, fresh):
        r = fresh.post(f"{API}/payments/checkout", json={"origin_url": BASE_URL}, timeout=T)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "checkout.stripe.com" in d["checkout_url"]
        tx = DB.payment_transactions.find_one({"session_id": d["session_id"]})
        assert tx["kind"] == "purchase" and tx["payment_status"] == "pending" and tx["user_id"] == fresh.uid
        st = requests.get(f"{API}/payments/status/{d['session_id']}", timeout=T).json()
        assert st["payment_status"] == "pending"

    def test_status_bad_404(self):
        assert requests.get(f"{API}/payments/status/cs_test_bogus123", timeout=T).status_code == 404

    def test_webhook_bad_sig_400(self):
        r = requests.post(f"{API}/stripe/webhook", data=b'{"type":"x"}', headers={"stripe-signature": "t=1,v1=bad", "Content-Type": "application/json"}, timeout=T)
        assert r.status_code == 400


# --- admin metrics
class TestMetrics:
    def test_metrics_shape(self, admin):
        r = admin.get(f"{API}/admin/metrics", timeout=T)
        assert r.status_code == 200
        d = r.json()
        assert [a["name"] for a in d["activation"]] == ["Signed up", "First read", "2nd situation or outcome in 7d", "Purchased", "First renewal"]
        assert isinstance(d["returning_days"], int)
        for row in d["by_origin"]:
            assert set(row) == {"origin", "signups", "first_read", "activated", "purchased", "renewed"}
        testers = DB.users.count_documents({"email": {"$regex": "(clerk_test|@example\\.com$)", "$options": "i"}})
        assert d["users"] <= DB.users.count_documents({}) - testers
        blob = json.dumps(d)
        assert "TEST_" not in blob and "forgot dinner" not in blob
        print("metrics users", d["users"], d["activation"], d["by_origin"], d["returning_days"])

    def test_metrics_requires_admin(self, fresh):
        assert fresh.get(f"{API}/admin/metrics", timeout=T).status_code == 403
