"""Actual non-C PostgreSQL collation cannot change archive manifest identity."""
import asyncio
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON, Binary, Int64, ObjectId
from bson.codec_options import CodecOptions
from bson.raw_bson import RawBSONDocument
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from backend import postgres_archive as archive
from backend.postgres_archive_diagnostics import _catalog_collation_order
from backend.postgres_access import identifier
from backend.postgres_codec import connection_options
from backend.postgres_snapshot import create_majority_barrier, pinned_source


DSN = os.getenv("IMLADRI_TEST_POSTGRES_DSN")
RS = os.getenv("IMLADRI_SNAPSHOT_TEST_MONGO_URL")
integration = pytest.mark.skipif(not DSN or not RS, reason="Needs explicit local PostgreSQL/replica set.")


@integration
def test_non_C_ICU_catalog_order_preserves_full_raw_BSON_and_metadata_parity():
    import pg8000.dbapi
    assert urlsplit(DSN).hostname in {"127.0.0.1", "localhost", "::1"}
    assert urlsplit(RS).hostname in {"127.0.0.1", "localhost", "::1"}
    options = connection_options(DSN, allow_local_plaintext=True)
    admin = pg8000.dbapi.connect(**options); admin.autocommit = True
    name = "fixture_archive_icu_" + uuid4().hex
    cursor = admin.cursor()
    cursor.execute("CREATE DATABASE " + identifier(name) + " TEMPLATE template0 LOCALE_PROVIDER icu ICU_LOCALE 'en-US'")
    cursor.close()
    owner = pg8000.dbapi.connect(**{**options, "database": name})
    try:
        async def run():
            client = AsyncIOMotorClient(RS, serverSelectionTimeoutMS=5000, connectTimeoutMS=20000, socketTimeoutMS=20000)
            source = client["archive_collation_fixture_" + uuid4().hex]
            try:
                mixed = ["listing_views", "listings", "Alpha", "alpha", "echo", "écho", "旧記録", "empty_collection"]
                for collection in mixed[:-1]:
                    await source[collection].insert_many([
                        {"_id": "same-id", "collection": collection, "typed": Int64(9), "nul": "x\x00y"},
                        {"_id": ObjectId("123456789012345678901234"), "binary": Binary(b"\x00\xff", 128)},
                    ])
                    await source[collection].create_index([("zulu", 1), ("alpha", -1)], name="original_compound")
                await source.create_collection(mixed[-1], validator={"$jsonSchema": {"bsonType": "object"}})
                barrier = await create_majority_barrier(source, fence_reference="local-ICU-archive",
                    application_writers_stopped=True, application_background_writers_stopped=True, ddl_stopped=True,
                    allow_local_plaintext=True)
                names = sorted(await source.list_collection_names())
                cursor = owner.cursor()
                cursor.execute("SELECT datlocprovider,to_jsonb(d)->>'datlocale',to_jsonb(d)->>'daticulocale' FROM pg_database d WHERE datname=current_database()")
                locale = cursor.fetchone()
                cursor.execute("SELECT value FROM unnest(%s::text[]) AS value ORDER BY value", (names,))
                sql_order = [row[0] for row in cursor.fetchall()]
                cursor.close(); owner.rollback()
                assert locale[0] == "i" and sql_order != names
                measured = _catalog_collation_order({**options, "database": name}, names)
                assert measured["transaction_read_only"] is True and measured["database_locale_provider"] == "i"
                assert measured["database_order_differs_from_codepoint"] is True
                assert measured["database_default_order_sha256"] == archive._manifest_hash(sql_order)
                assert measured["codepoint_order_sha256"] == archive._manifest_hash(names)
                print(json.dumps({"fixture_locale_provider": locale[0], "fixture_locale": locale[1] or locale[2],
                    "SQL_collection_order": sql_order, "Python_collection_order": names,
                    "order_differs": True}, ensure_ascii=True))
                async with pinned_source(source, barrier=barrier, collection_allowlist=names,
                        allow_local_plaintext=True) as view:
                    metadata, raw = {}, {}
                    for collection in names:
                        metadata[collection] = await view.collection_metadata(collection)
                        raw[collection] = sorted(row.raw for row in await view[collection].find({}).to_list(None))
                    result = await archive.archive_source(view, owner, source_alias="recovery_production")
                    assert result["receipt"]["source_two_pass_pinned_verified"] is True
                    assert result["receipt"]["target_two_pass_raw_verified"] is True
                    assert [row["name"] for row in result["receipt"]["collections"]] == names
                    cursor = owner.cursor()
                    for collection in names:
                        cursor.execute("SELECT raw_bson FROM imladri_archive.documents WHERE source_alias=%s AND collection=%s",
                            ("recovery_production", collection))
                        assert sorted(bytes(row[0]) for row in cursor.fetchall()) == raw[collection]
                        cursor.execute("SELECT spec_bson,indexes_bson FROM imladri_archive.collections WHERE source_alias=%s AND name=%s",
                            ("recovery_production", collection))
                        spec, indexes = cursor.fetchone()
                        assert bytes(spec) == metadata[collection]["spec_bson"]
                        packed = BSON(bytes(indexes)).decode(codec_options=CodecOptions(document_class=RawBSONDocument))
                        assert [row.raw for row in packed["indexes"]] == metadata[collection]["indexes_bson"]
                    cursor.close(); owner.rollback()
                    assert (await archive.archive_source(view, owner, source_alias="recovery_production"))["receipt"] == result["receipt"]
            finally:
                await client.drop_database(source.name); client.close()
        asyncio.run(run())
    finally:
        owner.close()
        cursor = admin.cursor(); cursor.execute("DROP DATABASE " + identifier(name)); cursor.close(); admin.close()
