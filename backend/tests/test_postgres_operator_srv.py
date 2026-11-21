"""Lazy SRV configuration must be verified before any Mongo network connection."""
from urllib.parse import parse_qs, urlsplit

import pytest
from pymongo import common, pool_shared
from pymongo.synchronous import mongo_client, pool, uri_parser

from backend import postgres_operator as worker
from backend.postgres_snapshot import SnapshotUnavailable


def prohibit_mongo_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Mongo connection/background work preceded transport verification")
    monkeypatch.setattr(mongo_client.MongoClient, "_get_topology", forbidden)
    monkeypatch.setattr(mongo_client.MongoClient, "_init_background", forbidden)
    monkeypatch.setattr(pool.Pool, "connect", forbidden)
    monkeypatch.setattr(pool_shared, "_configured_socket", forbidden)
    monkeypatch.setattr(pool_shared, "_create_connection", forbidden)


def configure_dns_only(monkeypatch):
    calls = []
    def resolve(uri, *args, **kwargs):
        calls.append(kwargs)
        options = {"tls": True}
        for key, values in parse_qs(urlsplit(uri).query).items():
            if key.lower().startswith("tls"):
                options[key.lower()] = values[0].lower() == "true"
        return {"nodelist": [("fixture-one.example.invalid", 27017), ("fixture-two.example.invalid", 27017)],
                "options": common._CaseInsensitiveDictionary(options)}
    monkeypatch.setattr(uri_parser, "_parse_srv", resolve)
    return calls


def config(query=""):
    return {"sources": {"personal_live": {"mongo_url": "mongodb+srv://fictional:fictional@fixture.example.invalid/" + query,
                                          "database": "imladri_personal_live"}}}


def test_lazy_srv_uses_dns_only_then_strict_tls_and_real_seed_ports(monkeypatch):
    prohibit_mongo_connections(monkeypatch)
    calls = configure_dns_only(monkeypatch)
    client, database, transport = worker._mongo(config(), "personal_live")
    try:
        assert transport["verified_tls"] is True
        assert transport["explicit_loopback_plaintext_fixture"] is False
        assert database.name == "imladri_personal_live"
        assert client.delegate._opened is False
        assert client.delegate._topology is None
        assert len(client.delegate._topology_settings.seeds) == 2
        assert all(type(port) is int for _, port in client.delegate._topology_settings.seeds)
        assert calls == [{"validate": True, "warn": True, "normalize": False,
            "connect_timeout": 15.0, "srv_service_name": None, "srv_max_hosts": None,
            "srv_allowed_hosts_suffix": None}]
        assert client.options.timeout is None
        assert client.options.pool_options.socket_timeout == 20
        assert client.options.server_selection_timeout == 15
    finally:
        client.close()


@pytest.mark.parametrize("query", ["?tls=false", "?tlsAllowInvalidCertificates=true", "?tlsAllowInvalidHostnames=true"])
def test_srv_resolution_does_not_relax_tls_or_hostname_checks(monkeypatch, query):
    prohibit_mongo_connections(monkeypatch)
    configure_dns_only(monkeypatch)
    with pytest.raises(SnapshotUnavailable, match="verified TLS|certificate and hostname"):
        worker._mongo(config(query), "personal_live")


def test_dns_failure_never_opens_mongo_transport(monkeypatch):
    prohibit_mongo_connections(monkeypatch)
    def failed(*args, **kwargs):
        raise RuntimeError("fictional DNS failure")
    monkeypatch.setattr(uri_parser, "_parse_srv", failed)
    with pytest.raises(RuntimeError, match="fictional DNS failure"):
        worker._mongo(config(), "personal_live")


def test_direct_plaintext_remains_rejected():
    value = config()
    value["sources"]["personal_live"]["mongo_url"] = "mongodb://127.0.0.1:62973/?directConnection=true"
    with pytest.raises(SnapshotUnavailable, match="verified TLS"):
        worker._mongo(value, "personal_live")
