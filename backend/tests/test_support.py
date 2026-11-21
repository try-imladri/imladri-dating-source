import asyncio
import os
from datetime import datetime, timedelta, timezone
from secrets import token_hex
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo.errors import DuplicateKeyError

from backend.support import SUPPORT_INDEXES, receipt_id, support_router
from backend.tests.test_free_plan_quota import run_case


@pytest.mark.parametrize('concurrent_count,expected_status,final_count', [(1, 201, 2), (12, 429, 12)])
def test_first_bucket_creation_race_only_rejects_a_truly_full_bucket(monkeypatch, concurrent_count, expected_status, final_count):
    """Force the actual first-upsert race while keeping the Mongo increment real."""
    async def check(db):
        app = FastAPI()
        app.include_router(support_router(db, lambda: {'role': 'admin'}))
        collection_type = type(db.support_rate)
        update = collection_type.update_one
        raced = False

        async def competing_first_writer(collection, query, changes, *args, **kwargs):
            nonlocal raced
            if collection.name == 'support_rate' and kwargs.get('upsert') and not raced:
                raced = True
                # The competing request creates the unique bucket after this
                # request's no-match check but before its attempted insert.
                await update(collection, {'_id': query['_id']}, {'$setOnInsert': {
                    'count': concurrent_count, 'expires_at': datetime.now(timezone.utc) + timedelta(hours=2)}}, upsert=True)
                raise DuplicateKeyError('Simulated first-upsert collision against the real unique bucket')
            return await update(collection, query, changes, *args, **kwargs)

        monkeypatch.setattr(collection_type, 'update_one', competing_first_writer)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            body = {'receipt': token_hex(32), 'category': 'Other', 'message': 'Fixture request'}
            response = await client.post('/api/support/requests', json=body)
            assert response.status_code == expected_status, response.text
            assert raced
            assert (await db.support_rate.find_one({}))['count'] == final_count
            assert await db.support_requests.count_documents({}) == (1 if expected_status == 201 else 0)
    run_case(check)


def test_concurrent_first_requests_admit_exactly_the_hourly_allowance():
    async def check(db):
        app = FastAPI()
        app.include_router(support_router(db, lambda: {'role': 'admin'}))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            responses = await asyncio.gather(*(client.post('/api/support/requests', json={
                'receipt': token_hex(32), 'category': 'Other', 'message': 'Fixture request'}) for _ in range(18)))
        codes = [response.status_code for response in responses]
        assert codes.count(201) == 12 and codes.count(429) == 6, codes
        assert await db.support_requests.count_documents({}) == 12
        assert (await db.support_rate.find_one({}))['count'] == 12
    run_case(check)


def test_private_receipts_and_staff_replies():
    uri = os.getenv("IMLADRI_TEST_MONGO")
    if not uri:
        pytest.skip("Set IMLADRI_TEST_MONGO to a local test MongoDB")

    async def run():
        mongo = AsyncIOMotorClient(uri)
        db = mongo["imladri_test_support_" + uuid4().hex]
        app = FastAPI()

        def admin(authorization: str = Header(default="")):
            if authorization not in ("Bearer staff-fixture", "Bearer teammate"):
                raise HTTPException(403)
            return {"role": "admin", "user_id": authorization.split()[1]}

        app.include_router(support_router(db, admin))
        try:
            for name, indexes in SUPPORT_INDEXES.items():
                await db[name].create_indexes(indexes)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                body = {"receipt": token_hex(32), "category": "Refund", "message": "Synthetic refund request"}
                key = {"receipt": body["receipt"]}
                staff = {"Authorization": "Bearer staff-fixture"}
                responses = await asyncio.gather(*(client.post("/api/support/requests", json=body) for _ in range(3)))
                assert all(response.status_code == 201 for response in responses), [(response.status_code, response.json()) for response in responses]
                assert await db.support_requests.count_documents({}) == 1
                assert (await client.post("/api/support/requests", json={**body, "message": "Another request"})).status_code == 409
                assert (await client.get("/api/admin/support")).status_code == 403
                inbox = (await client.get("/api/admin/support", headers=staff)).json()
                assert len(inbox) == 1 and body["receipt"] not in str(inbox)
                ticket_id = inbox[0]["id"]
                assert (await client.post("/api/support/status", json={"receipt": ticket_id})).status_code == 404
                assert (await client.put("/api/admin/support/" + ticket_id, json={"answer": "Done"})).status_code == 403
                assert (await client.put("/api/admin/support/" + ticket_id, json={"answer": "Done"}, headers=staff)).status_code == 200
                assert (await client.put("/api/admin/support/" + ticket_id, json={"answer": "Overwrite"}, headers=staff)).status_code == 409
                reply = await client.post("/api/support/status", json=key)
                assert reply.headers["cache-control"] == "no-store" and reply.json()["answer"] == "Done"
                follow = {**key, "round": 0, "message":"One more question"}
                assert (await client.post("/api/support/follow-up",json=follow)).is_success
                assert (await client.post("/api/support/follow-up",json=follow)).is_success
                assert (await client.post("/api/support/follow-up",json={**follow,"message":"another"})).status_code == 409
                endpoint="/api/admin/support/"+ticket_id
                assert (await client.put(endpoint, headers=staff, json={"round": 0, "answer": "Done"})).is_success
                assert (await client.put(endpoint, headers={"Authorization": "Bearer teammate"}, json={"round": 0, "answer": "Done"})).status_code == 409
                reopened = (await client.get("/api/admin/support", headers=staff)).json()[0]
                assert reopened["round"] == 1 and reopened["status"] == "open" and "reply_receipts" not in reopened
                teammate={"Authorization":"Bearer teammate"}
                assert (await client.put(endpoint+"/assignment",headers=staff,json={"claim":True})).is_success
                assert (await client.put(endpoint+"/assignment",headers=teammate,json={"claim":True})).status_code == 409
                assert (await client.put(endpoint,headers=teammate,json={"round":1,"answer":"Another editor"})).status_code == 409
                assert (await client.put(endpoint,headers=staff,json={"round":0,"answer":"Stale edit"})).status_code == 409
                assert (await client.put(endpoint,headers=staff,json={"round":1,"answer":"Second answer"})).is_success
                updated=(await client.post("/api/support/status",json=key)).json()
                assert updated["created_at"].endswith(("Z","+00:00")) and all(e["at"].endswith(("Z","+00:00")) for e in updated["thread"])
                assert [e["text"] for e in updated["thread"]] == ["Done","One more question","Second answer"]
                assert "staff-fixture" not in str(updated) and "assigned_to" not in updated
                summary=(await client.get("/api/admin/support/summary",headers=staff)).json()
                assert summary["open"]==0 and summary["answered_with_time"]==1 and summary["mean_first_response_minutes"] is not None
                await db.support_requests.update_one({"_id": receipt_id(body["receipt"])}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
                assert (await client.post("/api/support/status", json=key)).status_code == 404
                assert (await client.post("/api/support/delete", json=key)).status_code == 200
                assert await db.support_requests.count_documents({}) == 0
                await db.support_rate.delete_many({})
                for _ in range(12):
                    assert (await client.post("/api/support/requests", json={**body, "receipt": token_hex(32)})).status_code == 201
                assert (await client.post("/api/support/requests", json={**body, "receipt": token_hex(32)})).status_code == 429
        finally:
            await mongo.drop_database(db.name)
            mongo.close()

    asyncio.run(run())
