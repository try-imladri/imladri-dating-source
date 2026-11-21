import httpx

from backend.tests.test_free_plan_quota import run_case


def test_conversation_history_pages_include_recent_and_older_rows(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        monkeypatch.setitem(server.app.dependency_overrides, server.subscribed_member, lambda: {"user_id": "member"})
        await db.people.insert_many([{"id": pid, "user_id": "member", "nickname": pid} for pid in ("one", "two")])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as http:
            for collection, count, limit, path, params in (
                (db.exchanges, 507, 500, "/api/people/one/exchanges", {}),
                (db.group_chats, 207, 200, "/api/group-chat", {"ids": "one,two"}),
            ):
                # Several older records can legitimately share their timestamp.
                await collection.insert_many([
                    {"id": f"row-{i:04}", "created_at": "2026-01-01T00:00:00+00:00", "user_id": "member",
                     "person_id": "one", "person_ids": ["one", "two"], "key": "one,two", "question": "test"}
                    for i in range(count)
                ])
                recent = (await http.get(path, params=params)).json()
                assert [row["id"] for row in recent] == [f"row-{i:04}" for i in range(count - limit, count)]
                boundary = recent[0]
                await collection.delete_one({"id": boundary["id"]})
                cursor = {**params, "before_id": boundary["id"], "before_time": boundary["created_at"]}
                earlier = (await http.get(path, params=cursor)).json()
                assert [row["id"] for row in earlier] == [f"row-{i:04}" for i in range(count - limit)]
                assert (await http.get(path, params={**params, "before_id": "row-0005"})).status_code == 422
            await db.people.update_one({"id": "two"}, {"$set": {"deleting": True}})
            assert (await http.get("/api/group-chat", params={"ids": "one,two"})).json() == []
            await db.people.delete_one({"id": "two"})
            assert (await http.get("/api/group-chat", params={"ids": "one,two"})).json() == []
    run_case(scenario)


def test_member_pages_followups_atomic_profile_and_uncertain_speakers(monkeypatch):
    from backend import server

    async def scenario(db):
        monkeypatch.setattr(server, "db", db)
        user = {"user_id": "owner", "role": "admin", "env": "preview"}
        monkeypatch.setitem(server.app.dependency_overrides, server.subscribed_member, lambda: user)
        monkeypatch.setitem(server.app.dependency_overrides, server.reply_member, lambda: user)
        await db.users.insert_one(dict(user))
        at = server.now()
        await db.people.insert_many([{"id": f"p-{i:03}", "user_id": "owner", "nickname": "Member",
            "rel_type": "friend", "stage": "good", "created_at": at, "last_activity": at} for i in range(203)])
        pid = "p-000"
        base = {"created_at": at, "user_id": "owner", "person_id": pid}
        for kind, count in (("chat_groups", 53), ("practice", 103), ("memories", 1003), ("exchanges", 503),
                            ("preps", 503), ("corrections", 503)):
            await db[kind].insert_many([{**base, "id": f"{i:04}", "name": "Group", "person_ids": [pid,"p-001"],
                "topic": "Session", "text": "my saved record", "question": "My question", "situation": "My plan",
                "choice": {"label": "Check back"}, "proposed_memories": [{"text": "Ask later"}]} for i in range(count)])
            await db[kind].insert_one({**base, "id": "unrelated", "user_id": "outsider"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://fixture") as http:
            async def get(path, **params):
                response = await http.get("/api"+path, params=params)
                assert response.status_code == 200, (path,response.text)
                return response.json()

            async def verify(path, total, size, key="items", **filters):
                seen = set()
                cursor = ""
                while True:
                    packet = await get(path, page_size=size, cursor=cursor, **filters)
                    ids = {row["id"] for row in packet[key]}
                    assert not ids.intersection(seen) and len(ids) <= size and "unrelated" not in ids
                    assert packet["total"] == total
                    seen.update(ids)
                    cursor = packet["next_cursor"]
                    if not cursor:
                        break
                assert len(seen) == total

            await verify("/people", 203, 200, "people")
            await verify("/chat-groups", 53, 50)
            await verify("/practice", 103, 100, person_id=pid)
            for name,total in [("memories",1003),("exchanges",503),("preps",503),("practice",103),("corrections",503)]:
                await verify(f"/people/{pid}/saved/{name}", total, 100)
            counts = (await get("/people", page_size=1))["active_counts"]
            assert counts["all"] == 203
            await db.people.update_one({"id":"p-001"}, {"$set":{"archived":True,"nickname":"Archived [Sam]"}})
            await db.people.insert_one({"id":"other-name","user_id":"outsider","nickname":"Other [Sam]"})
            assert (await get("/people",page_size=1))["active_counts"]["all"] == 202
            match = await get("/people/lookup",q="[sam]")
            assert [p["id"] for p in match["items"]] == ["p-001"] and match["items"][0]["archived"]
            await db.people.update_one({"id":"p-001"}, {"$set":{"archived":False}})
            assert (await get("/people",page_size=1))["active_counts"]["all"] == 203
            await db.chat_groups.update_one({"id":"0052"},{"$set":{"name":"Earlier [Group]"}})
            assert [g["id"] for g in (await get("/chat-groups",page_size=1,q="[group]"))["items"]] == ["0052"]
            assert (await get(f"/people/{pid}/preps/0000"))["id"] == "0000"
            assert (await http.get("/api/people/p-001/preps/0000")).status_code == 404
            invalid = await http.get("/api/chat-groups", params={"page_size":10,"cursor":"malformed"})
            assert invalid.status_code == 422
            # A deleted boundary does not break the embedded cursor or repeat an item with an identical timestamp.
            packet = await get("/practice", person_id=pid, page_size=100)
            await db.practice.delete_one({"id":packet["items"][-1]["id"]})
            after = await get("/practice",person_id=pid,page_size=100,cursor=packet["next_cursor"])
            exported = (await http.get(f"/api/people/{pid}/export")).json()
            for category in ("practice","exchanges","preps","memories","corrections"):
                packet = await get(f"/people/{pid}/saved/{category}",page_size=1)
                assert len(exported[category]) == packet["total"]
                assert all(row.get("user_id") != "outsider" for row in exported[category])
            assert len(after["items"]) == 3 and not {row["id"] for row in after["items"]}.intersection(row["id"] for row in packet["items"])
            summary = await get("/overview")
            assert summary["checkin_count"] == summary["review_count"] == 503
            assert len(summary["checkins"]) == len(summary["unreviewed"]) == 5
            assert (await get(f"/people/{pid}/exchanges/0000"))["id"] == "0000"
            assert (await http.get("/api/people/p-001/exchanges/0000")).status_code == 404
            assert (await http.get(f"/api/people/{pid}/exchanges/unrelated")).status_code == 404
            profile = "/api/people/" + pid
            assert (await http.patch(profile,json={"nickname":"Atomic","demographics":{"age":30}})).status_code == 200
            assert (await http.patch(profile,json={"nickname":"Wrong","demographics":{"age":"invalid"}})).status_code == 422
            saved = await get("/people/"+pid)
            assert (saved["nickname"], saved["demographics"]["age"]) == ("Atomic",30)
            for speaker in ("unknown","other"):
                body = {"conversation":[{"speaker":speaker,"text":"uncertain text"}]}
                assert (await http.post(profile+"/exchanges",json=body)).status_code == 422
            assert await db.exchanges.count_documents({"person_id":pid,"user_id":"owner"}) == 503
            assert server.transcript_lines([{"speaker":"them","text":"first","sure":False},{"speaker":"unknown","text":"second"},
                                            {"speaker":"me","text":"third"}]) == [
                {"speaker":"unknown","text":"first","sure":False},{"speaker":"unknown","text":"second","sure":False},
                {"speaker":"unknown","text":"third","sure":False}]
    run_case(scenario)
