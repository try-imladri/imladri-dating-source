# Iteration 14: /me/type-compare?gender=male|female on a dedicated TEST user with a picked type (separate caches)
import json
import subprocess
import time

import pytest
import requests

BASE_URL = None
for line in open("/app/frontend/.env"):
    if line.startswith("REACT_APP_BACKEND_URL="):
        BASE_URL = line.split("=", 1)[1].strip()
API = f"{BASE_URL.rstrip('/')}/api"
EMAIL = "tester+tc14_clerk_test@example.com"


@pytest.fixture(scope="module")
def s():
    out = json.loads(subprocess.check_output(["python", "/app/scripts/clerk_test_user.py", EMAIL], text=True))
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {out['token']}", "Content-Type": "application/json"})
    sess.get(f"{API}/me")
    assert sess.put(f"{API}/me/about", json={"mbti": "INFJ", "answers": {}}).status_code == 200
    return sess


def test_female_then_male_separate(s):
    f = s.get(f"{API}/me/type-compare?gender=female", timeout=150)
    assert f.status_code == 200, f.text
    f = f.json()
    assert f["gender"] == "female" and f["code"] == "INFJ"
    assert len(f["items"]) >= 40 and all(101 <= i["number"] <= 150 for i in f["items"])
    m = s.get(f"{API}/me/type-compare?gender=male", timeout=150).json()
    assert m["gender"] == "male" and len(m["items"]) >= 40 and all(i["number"] <= 47 for i in m["items"])
    t = time.time()
    f2 = s.get(f"{API}/me/type-compare?gender=female", timeout=60).json()
    assert time.time() - t < 5
    assert [i["number"] for i in f2["items"]] == [i["number"] for i in f["items"]]
    bogus = s.get(f"{API}/me/type-compare?gender=xyz", timeout=60).json()
    assert bogus["gender"] == "male"
