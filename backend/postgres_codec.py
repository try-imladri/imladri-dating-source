"""Domain-independent lossless BSON and verified PostgreSQL connection settings."""
import hashlib
import ipaddress
import json
import re
import ssl
from urllib.parse import parse_qs, unquote, urlsplit

from bson import BSON, json_util
from bson.raw_bson import RawBSONDocument


SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")


def validate_name(value):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ValueError("Expected a bounded migration namespace or collection identifier.")
    return value


def canonical_document(document):
    return json_util.dumps(document, json_options=json_util.CANONICAL_JSON_OPTIONS,
                           ensure_ascii=True, separators=(",", ":"))


def document_key(document):
    if "_id" not in document:
        raise ValueError("Source records must have a document identifier.")
    return hashlib.sha256(BSON.encode({"_id": document["_id"]})).hexdigest()


def raw_document(document):
    return document.raw if isinstance(document, RawBSONDocument) else BSON.encode(document)


def document_hash(document):
    return hashlib.sha256(raw_document(document)).hexdigest()


def jsonb_compatible(value):
    if isinstance(value, str):
        return "\x00" not in value
    if isinstance(value, dict):
        return all(jsonb_compatible(key) and jsonb_compatible(item) for key, item in value.items())
    if isinstance(value, list):
        return all(jsonb_compatible(item) for item in value)
    return True


def jsonb_text(document):
    text = canonical_document(document)
    return text if jsonb_compatible(json.loads(text)) else None


def connection_options(dsn, *, allow_local_plaintext=False):
    url = urlsplit(dsn)
    if url.scheme not in ("postgres", "postgresql") or not url.hostname or not url.username or not url.path.strip("/"):
        raise ValueError("Expected a PostgreSQL DSN with a database and user.")
    options = parse_qs(url.query)
    if set(options) - {"sslmode"} or any(len(value) != 1 for value in options.values()):
        raise ValueError("Unsupported PostgreSQL DSN options.")
    mode = options.get("sslmode", ["verify-full"])[0]
    try:
        local = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        local = url.hostname == "localhost"
    if mode == "disable":
        if not (allow_local_plaintext and local):
            raise ValueError("Plaintext PostgreSQL is restricted to explicitly allowed loopback fixtures.")
        context = False
    elif mode in ("verify-full", "require"):
        context = ssl.create_default_context()
    else:
        raise ValueError("PostgreSQL requires verified TLS or an explicit loopback fixture.")
    return {"host": url.hostname, "port": url.port or 5432, "user": unquote(url.username),
            "password": unquote(url.password or ""), "database": unquote(url.path[1:]),
            "ssl_context": context, "timeout": 65, "application_name": "imladri-postgres-shadow"}
