# Iteration 24: Admin live activity feed (GET /api/admin/activity) + Quiet (generic) notifications
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

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
NICK = "TEST_Zephyr24"

load_dotenv("/app/backend/.env")
DB = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _sess(email):
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", email], text=True))
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    assert s.get(f"{API}/me").status_code == 200
    return s


@pytest.fixture(scope="module")
def admin():
    return _sess("tester+admin_clerk_test@example.com")


@pytest.fixture(scope="module")
def member():
    email = f"tester+it24{uuid.uuid4().hex[:6]}_clerk_test@example.com"
    sess = _sess(email)
    sess.email = email
    yield sess
    sess.delete(f"{API}/me")


@pytest.fixture(scope="module")
def qs(member):
    r = member.post(f"{API}/quickstart", json={"nickname": NICK, "what_happened": "TEST he went quiet after our date, Zephyr said he'd text."}, timeout=T)
    assert r.status_code == 200, r.text
    d = r.json()
    yield d
    member.delete(f"{API}/people/{d['person']['id']}")


# ---- admin activity: auth ----
def test_activity_unauth():
    assert requests.get(f"{API}/admin/activity").status_code == 401


def test_activity_member_forbidden(member):
    assert member.get(f"{API}/admin/activity").status_code == 403


def test_activity_shape(admin):
    r = admin.get(f"{API}/admin/activity")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) >= {"items", "last24", "now"}
    assert set(d["last24"]) == {"signup", "chat", "group", "practice"}
    assert all(isinstance(v, int) for v in d["last24"].values())
    assert len(d["items"]) <= 60
    for i in d["items"]:
        assert set(i) == {"type", "user", "at", "detail"}, i
        assert i["type"] in ("signup", "chat", "group", "practice")
    ats = [i["at"] for i in d["items"]]
    assert ats == sorted(ats, reverse=True)


# ---- activity: new chat + practice appear, no nickname/text ----
def test_activity_shows_new_chat_and_practice(admin, member, qs):
    pid = qs["person"]["id"]
    r = member.post(f"{API}/practice", json={"person_id": pid, "topic": "TEST secret topic Zephyr", "mode": "live"})
    assert r.status_code == 200, r.text
    d = admin.get(f"{API}/admin/activity").json()
    mine = [i for i in d["items"] if i["user"] == member.email]
    types = {i["type"] for i in mine}
    assert "chat" in types and "practice" in types and "signup" in types, mine
    assert any(i["detail"] == "Practice: live" for i in mine)
    assert any(i["detail"] in ("Asked a question", "Shared a conversation") for i in mine)
    assert d["items"][0]["user"] == member.email and d["items"][0]["type"] == "practice"
    blob = json.dumps(d)
    assert "Zephyr" not in blob and "secret topic" not in blob and "went quiet" not in blob
    assert d["last24"]["practice"] >= 1 and d["last24"]["chat"] >= 1


# ---- quiet notifications ----
def test_checkin_notification_generic(member, qs):
    eid = qs["exchange"]["id"]
    assert member.post(f"{API}/exchanges/{eid}/choice", json={"label": "TEST Text Zephyr first", "custom": False}).status_code == 200
    old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    DB.exchanges.update_one({"id": eid, "user_id": qs["person"]["user_id"]} if qs["person"].get("user_id") else {"id": eid}, {"$set": {"choice.at": old}})
    d = member.get(f"{API}/notifications").json()
    ci = [n for n in d["items"] if n["type"] == "checkin"]
    assert ci, d
    assert ci[0]["title"] == "A check-in is waiting"
    assert ci[0]["body"] == "Tap to log how it went."
    assert ci[0]["link"].startswith(f"/app/person/{qs['person']['id']}")
    assert "Zephyr" not in json.dumps(d)


def test_old_stored_notifications_masked(member, qs):
    me = member.get(f"{API}/me").json()
    uid = me.get("user_id") or me.get("user", {}).get("user_id")
    assert uid
    pid = qs["person"]["id"]
    now = datetime.now(timezone.utc).isoformat()
    DB.notifications.insert_many([
        {"id": "TEST_" + uuid.uuid4().hex, "user_id": uid, "type": "report", "title": f"{NICK}'s read was updated", "body": "Zephyr seems distant",
         "link": f"/app/person/{pid}?tab=report", "read": False, "created_at": now, "key": "TEST_" + uuid.uuid4().hex},
        {"id": "TEST_" + uuid.uuid4().hex, "user_id": uid, "type": "checkin", "title": f"How did it go with {NICK}?", "body": "You said: Text Zephyr",
         "link": f"/app/person/{pid}?tab=history", "read": False, "created_at": now, "key": "TEST_" + uuid.uuid4().hex},
        {"id": "TEST_" + uuid.uuid4().hex, "user_id": uid, "type": "announcement", "title": "TEST_Announce title", "body": "TEST_Announce body",
         "link": "/app", "read": False, "created_at": now, "key": "TEST_" + uuid.uuid4().hex},
        {"id": "TEST_" + uuid.uuid4().hex, "user_id": uid, "type": "new_page", "title": "New archetype: TEST_Type", "body": "TEST one liner",
         "link": "/types/x", "read": False, "created_at": now, "key": "TEST_" + uuid.uuid4().hex},
    ])
    d = member.get(f"{API}/notifications").json()
    blob = json.dumps([n for n in d["items"] if n["type"] in ("report", "checkin")])
    assert "Zephyr" not in blob
    rep = [n for n in d["items"] if n["type"] == "report"]
    assert rep and all(n["title"] == "A read was updated" for n in rep)
    assert all(n["link"].startswith(f"/app/person/{pid}") for n in rep)
    ann = [n for n in d["items"] if n["type"] == "announcement"]
    assert ann and ann[0]["title"] == "TEST_Announce title" and ann[0]["body"] == "TEST_Announce body"
    npg = [n for n in d["items"] if n["type"] == "new_page"]
    assert npg and npg[0]["title"] == "New archetype: TEST_Type"
    # read one still works
    assert member.post(f"{API}/notifications/{rep[0]['id']}/read").status_code == 200


def test_real_report_notification_generic(member, qs):
    pid = qs["person"]["id"]
    for _ in range(36):
        if member.get(f"{API}/people/{pid}").json().get("report_status") == "ready":
            break
        time.sleep(5)
    else:
        pytest.skip("first report never ready")
    eid = qs["exchange"]["id"]
    r = member.post(f"{API}/exchanges/{eid}/outcome", json={"result": "better", "text": "TEST Zephyr replied nicely"})
    assert r.status_code == 200, r.text
    for _ in range(36):
        items = [n for n in member.get(f"{API}/notifications").json()["items"] if n["type"] == "report" and not n["id"].startswith("TEST_")]
        if items:
            break
        time.sleep(5)
    assert items, "no report notification generated"
    assert items[0]["title"] == "A read was updated"
    assert "Zephyr" not in json.dumps(items)
    stored = DB.notifications.find_one({"id": items[0]["id"]}, {"_id": 0})
    assert "Zephyr" not in json.dumps(stored), stored


# ---- regression: admin metrics ----
def test_admin_metrics_regression(admin):
    r = admin.get(f"{API}/admin/metrics")
    assert r.status_code == 200, r.text
