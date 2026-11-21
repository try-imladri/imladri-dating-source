import json
import os
import sys
import urllib.request
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
SK = os.environ["CLERK_SECRET_KEY"]


def bapi(path, body=None, method="POST"):
    req = urllib.request.Request("https://api.clerk.com/v1" + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + SK, "Content-Type": "application/json", "User-Agent": "emergent-test"})
    return json.load(urllib.request.urlopen(req))


def user_token(email):
    users = bapi(f"/users?email_address={urllib.request.quote(email)}", method="GET")
    user = users[0] if users else bapi("/users", {"email_address": [email], "password": "Test-pass-12345!", "skip_password_checks": True})
    session = bapi("/sessions", {"user_id": user["id"]})
    token = bapi(f"/sessions/{session['id']}/tokens", {"expires_in_seconds": 3600})["jwt"]
    ticket = bapi("/sign_in_tokens", {"user_id": user["id"], "expires_in_seconds": 3600})["token"]
    return user["id"], token, ticket


if __name__ == "__main__":
    uid, tok, ticket = user_token(sys.argv[1])
    print(json.dumps({"user_id": uid, "token": tok, "ticket": ticket}))
