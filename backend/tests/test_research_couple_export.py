"""Individual releases, paired research consent and check-in reveal through the normal journey."""
import csv
import io
import os
import re
import time
from datetime import date, datetime, timedelta

import httpx
import pytest
from fastapi import Request

from backend import couples, couples_journey as journey, research_couple_export as export, research_rows
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.skipif(not os.getenv("IMLADRI_TEST_MONGO_URL"), reason="Requires isolated MongoDB aggregation.")
def test_pair_csv_contains_only_current_mutual_releases_and_dual_consented_links(monkeypatch):
    async def scenario(db):
        pair = "private-pair-id"
        first, second = "private-inviter-id", "private-invitee-id"
        env = "preview"
        eligible = {"env": {"$in": [env, None]}, "role": {"$ne": "admin"}, "closing": {"$ne": True},
                    "email": {"$not": re.compile("tester")}}
        stamp = "2026-11-09T00:00:00+00:00"
        secret = "PRIVATE_TYPED_MARKER"
        def grants(scope, digit):
            return {f"row_{scope}_code": digit * 32, f"row_{scope}_version": research_rows.VERSIONS[scope],
                    f"row_{scope}_env": env}
        users = [{"user_id": uid, "email": uid + "@qa.invalid", "env": env, "role": "member", "couples_adult_at": stamp,
                  "row_couple_pair_id": pair, **grants("couple", c), **grants("member", m),
                  "about": {"demographics": {"age": age, "height_cm": height, "ethnicity": "Prefer not to say"},
                            "private_context": secret}}
                 for uid, c, m, age, height in [(first, "1", "3", 30, 171), (second, "2", "4", 40, 189)]]
        users[1]["couples_adult_at"] = datetime.fromisoformat(stamp)  # legacy BSON UTC date
        now = int(time.time())
        users[0].update(tier="paid", stripe_subscription_id="sub_fixture", subscription_period_start=now - 60,
                        subscription_period_end=now + 3600)
        await db.users.insert_many(users)
        await db.couples_pairs.insert_one({"_id": pair, "state": "active", "env": env, "members": [first, second],
            "inviter":first, "invitee":second, "sponsor":first, "joined_at":stamp, "tone_sessions":{"private":secret}})
        code = research_rows.pair_code(pair, "1" * 32, "2" * 32)
        prompt = next(p for p in journey.PROMPTS if p["id"] == "support-1")
        key = prompt["id"]
        choice, other = prompt["choices"][:2]
        left, right = "m_" + "3"*32, "m_" + "4"*32
        async def rows(which=code):
            return [x async for x in export.iter_rows(db, eligible, stamp, which)]
        async def draft(user, line, picked=None, version=0):
            await journey.save_draft(db, user, key, journey.DraftIn(version=version, lines=[line], choice=picked))
        async def action(user, version, act, **params):
            await journey.change_thread(db, user, key, journey.Action(pair_id=pair, version=version, action=act, **params))
        def values(records, measure):
            return [(r["seat"], r["answer"]) for r in records if r["variable_id"] == measure]
        assert [x async for x in export.iter_codes(db, eligible)] == [code]
        # A malformed older pair or thread must not break the remaining units' bulk export.
        await db.couples_pairs.insert_one({"_id": "malformed", "state": "active", "env": env, "members": first,
                                         "inviter": first, "invitee": second})
        await db.couples_pairs.update_one({"_id": pair}, {"$set": {"journey.threads": ["bad"]}})
        assert [x async for x in export.iter_codes(db, eligible)] == [code]
        assert not [r for r in await rows(None) if r["domain"] == "couples"]
        shared = {"shares": {"a": ["private"], "b": ["private"]}}
        await db.couples_pairs.update_one({"_id": pair}, {"$set": {"journey.threads": {
            "bad1": {**shared, "prompt_id": [key], "agreements": "bad"},
            "bad2": {**shared, "kind": [], "card": {}, "agreements": [{"status": "active", "accepted": "bad"}]}}}})
        assert not [r for r in await rows(None) if r["domain"] == "couples"]
        await db.couples_pairs.update_one({"_id": pair}, {"$unset": {"journey": ""}})
        await db.couples_pairs.delete_one({"_id": "malformed"})
        initial = await rows()
        assert [x for x in initial if x["variable_id"] == "c:active"][0]["observed_on_utc"] == "2026-11-09"
        assert values(initial,"age_band") == [("inviter", "25-34"), ("invitee", "35-44")]
        assert values(initial,"height_band") == [("inviter", "170–179 cm"), ("invitee", "180–189 cm")]
        assert {r["status"] for r in initial if r["variable_id"] == "d:ethnicity"} == {"withheld"}
        assert {r["member_code"] for r in initial if r["domain"] == "demographics"} == {left, right}
        assert not [r for r in initial if r["domain"] == "couples"]
        await draft(users[0], choice, choice)
        await draft(users[1], other, None)  # Typed words matching an option are still free text.
        await action(users[0], 0, "share", kind="prompt", prompt_id=key, lines=[choice])
        assert not [r for r in await rows() if r["domain"] == "couples"]
        await action(users[1], 1, "share", kind="prompt", prompt_id=key, lines=[other])
        joined = await rows()
        assert next(r for r in joined if r["variable_id"] == "c:prompt:support-1")["observed_on_utc"]
        # A catalog change outside the normal versioned publisher must never relabel old answers.
        with monkeypatch.context() as edit:
            edit.setitem(prompt, "choices", [other, choice, *prompt["choices"][2:]])
            assert not values(await rows(), "c:prompt:support-1")
        assert values(joined, "c:prompt:support-1") == [("inviter",choice)]
        await draft(users[0], secret, other, version=1)
        assert values(await rows(), "c:prompt:support-1") == [("inviter",choice)]  # new private draft is invisible
        await action(users[0], 2, "propose", text=secret, review_on=date.today()+timedelta(days=1))
        thread = (await db.couples_pairs.find_one({"_id":pair}))["journey"]["threads"][key]
        aid = thread["agreements"][0]["id"]
        await action(users[1], 3, "accept", agreement_id=aid)
        await action(users[0], 4, "checkin", agreement_id=aid, result="didnt_help", share=True, note=secret)
        await action(users[1], 5, "checkin", agreement_id=aid, result="mixed", share=False, note=secret)
        assert not values(await rows(),"c:checkin:prompt:support-1")
        await action(users[1], 6, "checkin", agreement_id=aid, result="mixed", share=True, note=secret)
        records = await rows()
        assert values(records, "c:checkin:prompt:support-1") == [("inviter","didnt_help"),("invitee","mixed")]
        from backend import server
        monkeypatch.setattr(server, "db", db)
        async def signed_member(request: Request):
            return {"user_id": "fixture", "role": "admin" if request.headers.get("x-role") == "admin" else "member"}
        monkeypatch.setitem(server.app.dependency_overrides, server.current_member, signed_member)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as client:
            endpoint = "/api/admin/research/exports/couple"
            admin = {"x-role": "admin"}
            units = await client.get(endpoint + "/units", headers=admin)
            assert units.status_code == 200 and code in units.text
            assert (await client.get(endpoint + ".csv")).status_code == 403
            response = await client.get(endpoint + ".csv", headers=admin, params={"unit_code": code})
            assert response.is_success and "no-store" in response.headers["cache-control"]
            exported = list(csv.DictReader(io.StringIO(response.text)))
            assert [r["variable_id"] for r in exported] == [r["variable_id"] for r in records]
            assert {r["member_code"] for r in exported if r["seat"] == "joint"} == {""}
            assert all(x not in response.text for x in (secret, first, second, pair, aid))
            assert (await client.get(endpoint + ".csv", headers=admin, params={"unit_code": "c_" + "a" * 32})).status_code == 404
        assert all(x not in str(records) for x in (secret, first, second, pair, aid))
        by_agreement = [r for r in records if r["variable_id"] in ("c:checkin:prompt:support-1","c:agreement:prompt:support-1")]
        assert len(by_agreement) == 3 and len({r["observation_code"] for r in by_agreement}) == 1
        assert all(re.fullmatch(r"\d{4}-\d\d-\d\d", r["observed_on_utc"]) for r in by_agreement)
        assert {r.get("member_code") for r in records if r["seat"]=="joint"} == {None}
        await db.users.update_one({"user_id":first},{"$unset":{"row_member_version":""}})
        assert not any(r.get("member_code") for r in await rows())
        await action(users[0], 7, "withdraw_checkin", agreement_id=aid)
        assert not values(await rows(), "c:checkin:prompt:support-1")
        await action(users[1], 8, "withdraw")
        assert not [r for r in await rows() if r["domain"] == "couples"]
        for path, value in [("row_couple_pair_id","another-pair"),("row_couple_env","live"),("closing",True),
                            ("couples_adult_at",[stamp]), ("couples_adult_at",[datetime.fromisoformat(stamp)]),
                            ("role","admin"),("email","tester@example.invalid"),("about.demographics.age",17),
                            ("couples_blocked",[couples.block_key(first)])]:
            original = await db.users.find_one({"user_id":second})
            await db.users.update_one({"user_id":second},{"$set":{path:value}})
            assert await rows() == [] and [x async for x in export.iter_codes(db, eligible)] == []
            await db.users.replace_one({"_id":original["_id"]},original)
        await db.users.update_one({"user_id":second},{"$unset":{"row_couple_code":""}})
        assert await rows() == [] and await rows(None) == []
        await db.users.update_one({"user_id":second},{"$set":{"row_couple_code":"5"*32}})
        assert await rows() == [] and len(await rows(None)) > 0
        await db.couples_pairs.update_one({"_id":pair},{"$set":{"state":"closed"}})
        assert await rows(None) == []
    run_case(scenario)
