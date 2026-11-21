"""Prompt default upgrades use actual PostgreSQL CAS and preserve editor choices."""
import asyncio
from copy import deepcopy
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from backend.content import DEFAULT_PROMPTS, PREVIOUS_PREPARE_PROMPT, PREVIOUS_SIX_PART, migrate_prompt_defaults
from backend.prompt_library import PREVIOUS_FEATURE_PROMPTS, normalized


FORMER = {**PREVIOUS_FEATURE_PROMPTS, "six_part": PREVIOUS_SIX_PART, "prepare": PREVIOUS_PREPARE_PROMPT}


def run_case(callback):
    dsn = os.environ.get("IMLADRI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Requires the owned isolated loopback PostgreSQL fixture.")
    assert urlsplit(dsn).hostname in ("localhost", "127.0.0.1", "::1")
    from backend.postgres_store import PostgresDatabase

    async def exercise():
        db = PostgresDatabase(dsn, "fixture_prompt_defaults_" + uuid4().hex, allow_local_plaintext=True)
        try:
            await callback(db)
        finally:
            def cleanup(cursor, _now):
                for table in ("rpc_requests", "unique_keys", "indexes", "documents"):
                    cursor.execute("DELETE FROM imladri_app." + table + " WHERE namespace=%s", (db.name,))
            await db._run(cleanup, write="__fixture_cleanup")
            await db.aclose()
    asyncio.run(exercise())


@pytest.mark.parametrize("key", sorted(FORMER))
def test_exact_shipped_prompt_upgrades_without_losing_other_editor_settings(key):
    async def case(db):
        editorial = "A deliberately custom instruction that must survive unrelated migrations."
        before = {"key": "global", "prompts": {key: FORMER[key], "starters": editorial},
                  "prompts_revision": "editorial-revision", "prompt_history": [{"revision": "retained"}]}
        await db.settings.insert_one(deepcopy(before))
        await migrate_prompt_defaults(db)
        after = await db.settings.find_one({"key": "global"}, {"_id": 0})
        assert after["prompts"][key] == DEFAULT_PROMPTS[key] != FORMER[key]
        assert after["prompts"]["starters"] == editorial
        assert after["prompts_revision"] == before["prompts_revision"]
        assert after["prompt_history"] == before["prompt_history"]
        await migrate_prompt_defaults(db)
        assert await db.settings.find_one({"key": "global"}, {"_id": 0}) == after
    run_case(case)


@pytest.mark.parametrize("key", sorted(FORMER))
def test_admin_edits_never_get_replaced_with_new_source_defaults(key):
    async def case(db):
        edited = FORMER[key] + " This administrator added a specific instruction."
        before = {"key": "global", "prompts": {key: edited}, "prompts_revision": "custom"}
        await db.settings.insert_one(deepcopy(before))
        await migrate_prompt_defaults(db)
        after = await db.settings.find_one({"key": "global"}, {"_id": 0})
        assert after == before
        assert normalized(DEFAULT_PROMPTS, after["prompts"])[key] == edited
    run_case(case)
