"""Datastore selection never reopens traffic with source/operator credentials."""
import pytest

from backend import database


TEMPORARY = ("MONGO_URL", "IMLADRI_POSTGRES_SHADOW_DSN", "IMLADRI_MIGRATION_SOURCES_JSON",
             "IMLADRI_MIGRATION_ROLE_DSNS_JSON", "IMLADRI_DATABASE_OPERATOR_SECRET",
             "ATLAS_NETWORK_CLIENT_ID", "ATLAS_NETWORK_CLIENT_SECRET")


@pytest.mark.parametrize("mode", ["postgresql", "render_gateway"])
@pytest.mark.parametrize("key", TEMPORARY)
def test_target_traffic_rejects_retired_or_operator_credentials_before_opening(mode, key, monkeypatch):
    for name in TEMPORARY:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", mode)
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "off")
    monkeypatch.setenv(key, "only-synthetic-credential")
    with pytest.raises(ValueError, match="Remove source and maintenance credentials"):
        database.open_database()


def test_paused_operator_keeps_source_credentials_without_opening_application_connections(monkeypatch):
    from backend import postgres_store
    constructed = []
    class Store:
        def __init__(self, *args):
            constructed.append(args)
    monkeypatch.setattr(postgres_store, "PostgresDatabase", Store)
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", "postgresql")
    monkeypatch.setenv("IMLADRI_DATABASE_MAINTENANCE", "paused")
    monkeypatch.setenv("MONGO_URL", "mongodb://not-a-real-source.invalid")
    monkeypatch.setenv("IMLADRI_POSTGRES_DSN", "only-synthetic-target")
    monkeypatch.setenv("IMLADRI_POSTGRES_NAMESPACE", "fixture")
    client, db = database.open_database()
    assert client is db and constructed == [("only-synthetic-target", "fixture")]


def test_unknown_mode_never_falls_back_to_mongodb(monkeypatch):
    monkeypatch.setenv("IMLADRI_DATABASE_BACKEND", "misspelled")
    with pytest.raises(ValueError, match="Unknown application database backend"):
        database.open_database()
