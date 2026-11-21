"""Synthetic votes exercise import/scoring; no study participant is simulated in production."""
import asyncio
import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from backend import human_review as hr


def packet(slot):
    return json.loads(re.search(r'<script type="application/json" id="packet">(.*?)</script>', hr.reader_html(slot), re.S).group(1))


def test_both_packets_are_blind_and_use_identical_evidence_in_different_orders():
    a, b = packet("A"), packet("B")
    assert a["version"] == b["version"] and len(a["items"]) == len(b["items"]) == 87
    assert a["items"] != b["items"] and sorted(a["items"], key=lambda r: r["id"]) == sorted(b["items"], key=lambda r: r["id"])
    assert all(not {"reference", "group", "slug", "readings", "votes"}.intersection(r) for r in a["items"])
    assert all(r["lines"] for r in a["items"]) and len(a["definitions"]) == 121


def test_ballots_are_immutable_isolated_and_never_unblinded_early():
    uri = os.getenv("IMLADRI_TEST_MONGO_URL")
    if not uri:
        pytest.skip("Set a disposable loopback MongoDB")
    assert urlsplit(uri).hostname in {"localhost", "127.0.0.1", "::1"}

    async def scenario():
        client = AsyncIOMotorClient(uri)
        db = client["review_ballots_qa_" + uuid4().hex]
        data, _, version = hr.study()
        def make(slot):
            choices = {r["id"]: {"choice": r["reference"], "reason": "Synthetic fixture only."} for r in data["items"]}
            return hr.Ballot(version=version, independent=True, slot=slot, votes=choices)
        try:
            a, b = make("A"), make("B")
            validate_bad = [a.model_copy(update={"independent": False}), a.model_copy(update={"version": "old"}), a.model_copy(update={"votes": {}})]
            for invalid in validate_bad:
                with pytest.raises(HTTPException) as caught:
                    await hr.save(db, "preview", invalid, "staff-test", "now")
                assert caught.value.status_code == 422
            item = next(r["id"] for r in data["items"] if r["kind"] == "tone")
            invalid_choice = make("A")
            invalid_choice.votes[item].choice = "not a rated tone"
            with pytest.raises(HTTPException, match="listed choice"):
                hr.validate(invalid_choice)
            result = await hr.save(db, "preview", a, "staff-test", "now")
            assert result["received"] == ["A"] and result["cases"] == [] and all(not x["rated_votes"] for x in result["groups"])
            assert result == await hr.save(db, "preview", make("A"), "staff-test", "later")
            a.votes[item].choice = hr.SKIP
            with pytest.raises(HTTPException) as caught:
                await hr.save(db, "preview", a, "staff-test", "later")
            assert caught.value.status_code == 409
            b.votes[item].choice = hr.SKIP
            result = await hr.save(db, "preview", b, "staff-test", "later")
            assert (await hr.results(db,"production"))["received"] == []
            groups = {g["group"]: g for g in result["groups"]}
            assert [groups[key]["total"] for key in ("tentative","holdout","tone")] == [59,16,12]
            assert result["guide_entries"] == 121 and result["selectable_names"] == 108
            assert groups["tentative"]["shared_reference_names"] == 59 and groups["holdout"]["shared_reference_names"] == 15
            assert len(result["cases"]) == 87 and groups["tone"]["both_assessed"] == groups["tone"]["agree"] == 11
            assert groups["tone"]["rated_votes"] == groups["tone"]["reference_matches"] == 23
        finally:
            await client.drop_database(db.name)
            client.close()

    asyncio.run(scenario())