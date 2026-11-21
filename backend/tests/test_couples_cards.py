import asyncio
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header
from motor.motor_asyncio import AsyncIOMotorClient

from backend.couples import COUPLES_INDEXES
from backend.couples_analytics import report
from backend.couples_catalog import CARDS, config_key
from backend.couples_journey import JOURNEY_INDEXES, journey_router


def test_four_card_journeys_and_admin_funnel(monkeypatch):
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO_URL to a disposable loopback MongoDB.")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}

    async def scenario():
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        db = client["couples_cards_test_" + uuid4().hex]
        app = FastAPI()

        async def member(x_member: str = Header()):
            return await db.users.find_one({"user_id": x_member})

        async def unexpected_generation(*_):
            pytest.fail("Card exercises must never make a provider call")

        app.include_router(journey_router(lambda: db, member, unexpected_generation))
        try:
            for collection, indexes in {**JOURNEY_INDEXES, **COUPLES_INDEXES}.items():
                await db[collection].create_indexes(indexes)
            t = int(time.time())
            people = [{"user_id": user, "env": "preview", "role": "member", "couples_adult_at": "2026-01-01T00:00:00+00:00"}
                      for user in ("one", "two", "outsider")]
            people[0].update(tier="paid", stripe_subscription_id="fixture", subscription_period_start=t - 600,
                             subscription_period_end=t + 86400)
            await db.users.insert_many(people)
            pair = "test-pair-one"
            model = {"state": "active", "env": "preview", "created_at": datetime.now(timezone.utc).isoformat(),
                     "joined_at": datetime.now(timezone.utc).isoformat(), "members": ["one", "two"],
                     "inviter": "one", "invitee": "two", "sponsor": "one"}
            await db.couples_pairs.insert_one({"_id": pair, **model})
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as api:
                async def req(who, method, path, body=None, status=200):
                    response = await api.request(method, "/couples" + path, headers={"x-member": who}, json=body)
                    assert response.status_code == status, (path, response.text)
                    return response.json()

                async def rows(**criteria):
                    result = await report(db, "preview", {"role": "member", **criteria}, 7)
                    assert all(w not in str(result) for w in ("private draft", "selected words", "test agreement", "review note"))
                    return {row["id"]: row for row in result["card_funnel"]}

                assert len(await rows()) == 4 and not any(row["started"] for row in (await rows()).values())
                for index, (slug, spec) in enumerate(CARDS.items()):
                    url = "/cards/" + slug + "/start"
                    origin = "sample" if index % 2 else "card"
                    args = {"pair_id": pair, "origin": origin}
                    await req("outsider", "POST", url, args, 404)
                    await req("one", "POST", url, {**args, "pair_id": "former"}, 409)
                    await req("one", "POST", url, {**args, "origin": "unknown"}, 422)
                    first, retry = await asyncio.gather(req("one", "POST", url, args), req("one", "POST", url, args))
                    assert first == retry
                    key = first["key"]
                    assert key not in str(await req("two", "GET", "/journey"))
                    assert (await rows())[slug]["from_" + origin] == 1
                    own_lines = ["selected words " + slug, "private draft " + slug]
                    peer_line = "second selected words " + slug
                    draft_url = "/drafts/" + key
                    await req("one", "PUT", draft_url, {"version": 0, "lines": own_lines})
                    await req("two", "PUT", draft_url, {"version": 0, "lines": [peer_line]}, 404)

                    async def action(who, version, which, **data):
                        return await req(who, "POST", "/threads/" + key, {"pair_id": pair, "version": version,
                                         "action": which, "request_id": uuid4().hex, **data})

                    def thread(view):
                        return next(item for item in view["threads"] if item["id"] == key)

                    await action("one", 0, "share", kind="prompt", lines=[own_lines[0]])
                    second = await req("two", "GET", "/journey")
                    assert thread(second)["partner_shared"] and not thread(second)["perspectives"]
                    assert all(line not in str(second) for line in own_lines)
                    assert (await rows())[slug]["mutual"] == 0
                    assert await req("two", "POST", url, {**args, "origin": "card"}) == first
                    if index == 0:
                        # Publishing an entirely different starter catalog must not retire an in-flight card.
                        await db.couples_settings.insert_one({"_id": config_key(), "published": []})
                    await req("two", "PUT", draft_url, {"version": 0, "lines": [peer_line]})
                    reveal = thread(await action("two", 1, "share", kind="prompt", lines=[peer_line]))
                    assert reveal["card"] == slug and reveal["title"] == spec["question"]
                    assert reveal["example_agreement"] == spec["agreement"] and reveal["discussion"] == spec["checkIn"]
                    assert own_lines[0] in str(reveal["perspectives"]) and own_lines[1] not in str(reveal)
                    for mode in ("private", "shared"):
                        await req("two", "POST", f"/tone/{mode}/{key}", {"pair_id": pair, "version": 1}, 422)
                    proposal = thread(await action("one", 2, "propose", text="test agreement " + slug,
                                                   review_on=(date.today() + timedelta(days=4)).isoformat()))
                    aid = proposal["agreements"][0]["id"]
                    assert (await rows())[slug]["agreed"] == 0
                    await action("two", 3, "accept", agreement_id=aid)
                    if index == 0:
                        due = datetime.combine(date.today() + timedelta(days=4), datetime.min.time(), tzinfo=timezone.utc)
                        visit = f"/threads/{key}/review-visit/{aid}/"
                        async def stats():
                            return (await db.couples_pairs.find_one({"_id": pair}))["stats"]
                        async def cohort(at):
                            return (await report(db, "preview", {"role": "member"}, 30, at))["scheduled_returns"]
                        with monkeypatch.context() as clock:
                            clock.setattr("backend.couples_journey.now", lambda: due.isoformat())
                            await req("outsider", "POST", visit + "1", status=404)
                            await req("one", "POST", visit + "1")
                            assert (await stats())["first_due_visits"] == {}
                            await db.users.update_many({"user_id": {"$in": ["one","two"]}},
                                                       {"$set": {"couples_preferences.review_return_opt_in": True,"couples_preferences.review_return_since":"2026-01-01T00:00:00+00:00"}})
                            clock.setattr("backend.couples_journey.now", lambda: (due-timedelta(seconds=1)).isoformat())
                            await req("one", "POST", visit + "1")
                            clock.setattr("backend.couples_journey.now", lambda: due.isoformat())
                            await req("one", "POST", visit + "2")
                            assert (await stats())["first_due_visits"] == {}
                            await req("one", "POST", visit + "1")
                            clock.setattr("backend.couples_journey.now", lambda: (due+timedelta(days=8)).isoformat())
                            await req("two", "POST", visit + "1")
                            assert list((await stats())["first_due_visits"].values()) == [due.isoformat()]
                            clock.setattr("backend.couples_journey.now", lambda: (due+timedelta(days=7)).isoformat())
                            await asyncio.gather(req("one", "POST", visit + "1"), req("two", "POST", visit + "1"))
                            values = sorted((await stats())["first_due_visits"].values())
                            assert values == [due.isoformat(), (due+timedelta(days=7)).isoformat()]
                        before_due = await cohort(due - timedelta(microseconds=1))
                        assert before_due["due"] == 0 and before_due["observed_so_far"]["eligible"] == 0
                        today = await cohort(due)
                        assert today["observed_so_far"] == {"eligible": 1, "returned": 1, "both_returned": 0, "both_shared": 0}
                        assert today["pending"] == 1 and today["returned"] == 0
                        pending = await cohort(due+timedelta(days=7))
                        assert pending["observed_so_far"] == {"eligible": 1, "returned": 1, "both_returned": 1, "both_shared": 0}
                        assert pending["pending"] == 1 and pending["eligible"] == 0
                        mature = await cohort(due+timedelta(days=8))
                        assert (mature["eligible"], mature["returned"], mature["both_returned"], mature["both_shared"]) == (1,1,1,0)
                        async def card_returns():
                            data = await report(db, "preview", {"role": "member"}, 30, due+timedelta(days=8))
                            return {row["id"]: row for row in data["card_scheduled_returns"]}
                        returned = await card_returns()
                        assert [k for k,r in returned.items() if r["eligible"]] == [slug]
                        assert returned[slug]["observed_so_far"] == mature["observed_so_far"]
                        assert returned[slug]["both_returned"] == 1
                        assert returned[slug]["outcomes"]["respondents"] is None
                        await db.users.update_one({"user_id":"two"},{"$set":{"couples_preferences.review_return_opt_in":False}})
                        assert not any(r["eligible"] for r in (await card_returns()).values())
                        withdrawn = await cohort(due+timedelta(days=8))
                        assert not any(withdrawn.pop("observed_so_far").values()) and not any(withdrawn.values())
                    await action("one", 4, "checkin", agreement_id=aid, result="mixed", note="review note 1", share=True)
                    check = thread(await req("two", "GET", "/journey"))["agreements"][0]
                    assert check["partner_checkin"] is None and (await rows())[slug]["reviewed"] == 0
                    await action("two", 5, "checkin", agreement_id=aid, result="helped", note="review note 2", share=False)
                    assert (await rows())[slug]["reviewed"] == 0
                    finished = thread(await action("two", 6, "checkin", agreement_id=aid, result="helped", note="review note 2", share=True))
                    assert finished["agreements"][0]["partner_checkin"]["note"] == "review note 1"
                    expected = dict(id=slug, name=spec["name"], started=1, mutual=1, agreed=1, reviewed=1,
                                    from_card=int(origin == "card"), from_sample=int(origin == "sample"))
                    assert (await rows())[slug] == expected
                    await action("one", 7, "withdraw")
                    assert not thread(await req("two", "GET", "/journey"))["perspectives"]
                    assert (await rows())[slug] == expected
                assert not any(row["started"] for row in (await rows(user_id="one")).values())
                await req("one", "POST", "/cards/unknown/start", {"pair_id": pair}, 404)
                # End the first pairing and establish a different pairing for the sponsor.
                await db.couples_pairs.update_one({"_id": pair}, {"$set": {"state": "closed"}, "$unset": {"journey": "", "members": ""}})
                await db.couples_pairs.insert_one({"_id": "second-pair", **model, "invitee": "outsider", "members": ["one", "outsider"]})
                next_card = await req("one", "POST", url, {"pair_id": "second-pair"})
                assert next_card["key"] != key
                own = await req("one", "GET", "/journey")
                assert any(p["id"] == key and p["retired"] for p in own["prompts"])
                assert "private draft" not in str(await req("outsider", "GET", "/journey"))
                await req("one", "POST", "/threads/" + key, {"action": "share", "kind": "prompt", "lines": [own_lines[0]],
                                                             "version": 0, "pair_id": "second-pair"}, 404)
                assert (await rows())[slug]["started"] == 2
                assert "payment_transactions" not in await db.list_collection_names()
                assert await db.users.count_documents({"$or": [
                    {"paid_reply_cycles": {"$exists": True}}, {"free_reply_requests": {"$exists": True}},
                    {"topup_balance": {"$exists": True}}]}) == 0
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(scenario())
