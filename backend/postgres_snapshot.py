"""Read-only, server-pinned MongoDB snapshot source for a bounded final copy.

Application/background writers and DDL still require an external barrier. The
native MongoDB TTL monitor may continue: every data read uses the same logical
majority-committed snapshot timestamp, selected by the server. Metadata reads
are explicitly outside that snapshot. There is no retry that advances its time.
"""
from contextlib import asynccontextmanager
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import re
import ssl
import time
from uuid import uuid4

from bson import BSON
from bson.codec_options import CodecOptions
from bson.raw_bson import RawBSONDocument
from bson.timestamp import Timestamp
from pymongo import _csot

from .postgres_migration import COLLECTION_DOMAINS, validate_name


RAW = CodecOptions(document_class=RawBSONDocument)


class SnapshotUnavailable(RuntimeError):
    """Sanitized snapshot failure, without source records or credentials."""


def _collection_name(name):
    if not isinstance(name, str) or not name or "\x00" in name or len(name.encode()) > 1024:
        raise SnapshotUnavailable("The reviewed MongoDB collection name is invalid or exceeds its bound.")


def _source_transport(database, *, allow_local_plaintext=False):
    client = database.client
    pool = client.options.pool_options
    try:
        seeds = sorted((str(host), int(port)) for host, port in client.delegate._topology_settings.seeds)
    except Exception:
        raise SnapshotUnavailable("The configured MongoDB source identity cannot be verified.") from None
    if not seeds:
        raise SnapshotUnavailable("The configured MongoDB source has no verified endpoints.")
    def local(host):
        if host.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host.strip("[]")).is_loopback
        except ValueError:
            return False
    context = pool._ssl_context
    if context is None:
        if allow_local_plaintext is not True or not all(local(host) for host, _ in seeds):
            raise SnapshotUnavailable("The source requires verified TLS; plaintext is only an explicit loopback fixture option.")
        transport = {"verified_tls": False, "explicit_loopback_plaintext_fixture": True}
    else:
        if context.verify_mode != ssl.CERT_REQUIRED or context.check_hostname is not True or pool.tls_allow_invalid_hostnames:
            raise SnapshotUnavailable("MongoDB certificate and hostname verification must both remain enabled.")
        transport = {"verified_tls": True, "explicit_loopback_plaintext_fixture": False}
    return {**transport, "configured_endpoints_sha256": hashlib.sha256(json.dumps(seeds, separators=(",", ":")).encode()).hexdigest()}


def _client_bounds(database, milliseconds, *, allow_local_plaintext=False):
    options = database.client.options
    # Manual getMore does not pass through the driver's cursor helper. CSOT
    # would add forbidden maxTimeMS to that command, including an ambient
    # pymongo.timeout context. Fail closed before using such a client/context.
    if options.timeout is not None or _csot.get_timeout() is not None:
        raise SnapshotUnavailable("A dedicated snapshot client cannot use configured or inherited CSOT timeouts.")
    waits = (options.pool_options.socket_timeout, options.pool_options.connect_timeout, options.server_selection_timeout)
    if any(not isinstance(value, (int, float)) or not 0 < value <= milliseconds / 1000 for value in waits):
        raise SnapshotUnavailable("The snapshot client requires finite socket, connection and selection deadlines within its operation bound.")
    return _source_transport(database, allow_local_plaintext=allow_local_plaintext)


def _barrier_hash(value):
    return hashlib.sha256(json.dumps({key: item for key, item in value.items() if key != "receipt_fingerprint"},
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def create_majority_barrier(database, *, fence_reference, application_writers_stopped,
                                  application_background_writers_stopped, ddl_stopped, max_time_ms=20000,
                                  allow_local_plaintext=False, control_database=None):
    """Commit a content-free control record AFTER the external writer barrier.

    The majority-acknowledged operation follows prior primary writes, including
    earlier w:1 acknowledgements. Its actual response timestamp, rather than a
    hello/cluster clock guess, becomes the required final-copy snapshot cutoff.
    """
    if (getattr(type(database), "backend", "mongodb") != "mongodb"
            or application_writers_stopped is not True or application_background_writers_stopped is not True
            or ddl_stopped is not True or not isinstance(fence_reference, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/\\-]{1,240}", fence_reference)
            or type(max_time_ms) is not int or not 1 <= max_time_ms <= 20000):
        raise SnapshotUnavailable("A majority barrier requires an explicit application/background/DDL writer fence.")
    transport = _client_bounds(database, max_time_ms, allow_local_plaintext=allow_local_plaintext)
    namespace = validate_name(database.name)
    control = database if control_database is None else control_database
    if control_database is not None and (control.client is not database.client or control.name != "imladri_migration_control"):
        raise SnapshotUnavailable("A separate barrier database must be the fixed control database on the same configured client.")
    identity = "postgres-cutover-barrier:" + uuid4().hex
    command = {"update": "database_migration_reports", "ordered": True,
        "updates": [{"q": {"_id": identity}, "upsert": True, "u": {
            "$setOnInsert": {"kind": "postgres_cutover_majority_barrier", "version": 1,
                             "fence_reference": fence_reference}, "$currentDate": {"committed_at": True}}}],
        "writeConcern": {"w": "majority", "wtimeout": max_time_ms}, "maxTimeMS": max_time_ms}
    session = None
    try:
        session = await database.client.start_session(causal_consistency=False)
        response = await asyncio.wait_for(control.command(command, session=session), timeout=max_time_ms / 1000)
        stamp = response.get("operationTime")
        if (response.get("ok") != 1 or response.get("writeErrors") or response.get("writeConcernError")
                or response.get("n") != 1 or not isinstance(stamp, Timestamp) or session.operation_time != stamp):
            raise SnapshotUnavailable("The metadata write did not prove a majority-acknowledged operation timestamp.")
    except SnapshotUnavailable:
        raise
    except Exception:
        raise SnapshotUnavailable("The majority metadata barrier did not complete; no cutoff is authorized.") from None
    finally:
        if session is not None:
            try:
                await asyncio.wait_for(session.end_session(), timeout=2)
            except Exception:
                pass
    result = {"version": 1, "kind": "majority_write_barrier", "source": {"backend": "mongodb", "namespace": namespace},
        "fence_reference": fence_reference, "control_record_id": identity, "acknowledged": True,
        "control_database": control.name, "source_database_write_operations": int(control.name == database.name),
        "source_transport": transport,
        "write_concern": {"w": "majority", "wtimeout_ms": max_time_ms},
        "operation_time": {"time": stamp.time, "increment": stamp.inc},
        "session_operation_time_verified": True,
        "application_writers_stopped_asserted": True, "application_background_writers_stopped_asserted": True,
        "ddl_stopped_asserted": True, "native_ttl_monitor_stopped": False,
        "application_content_mutated": False, "credentials_or_record_values_included": False}
    result["receipt_fingerprint"] = _barrier_hash(result)
    return result


def _validate_barrier(database, barrier, *, allow_local_plaintext=False):
    if (not isinstance(barrier, dict) or barrier.get("kind") != "majority_write_barrier"
            or barrier.get("source") != {"backend": "mongodb", "namespace": validate_name(database.name)}
            or barrier.get("acknowledged") is not True or barrier.get("write_concern", {}).get("w") != "majority"
            or barrier.get("session_operation_time_verified") is not True
            or barrier.get("source_transport") != _source_transport(database, allow_local_plaintext=allow_local_plaintext)
            or barrier.get("receipt_fingerprint") != _barrier_hash(barrier)
            or any(barrier.get(key) is not True for key in ("application_writers_stopped_asserted",
                "application_background_writers_stopped_asserted", "ddl_stopped_asserted"))):
        raise SnapshotUnavailable("The majority writer-barrier receipt is missing, changed or belongs to another source.")
    stamp = barrier.get("operation_time", {})
    if type(stamp.get("time")) is not int or type(stamp.get("increment")) is not int:
        raise SnapshotUnavailable("The majority writer-barrier timestamp is invalid.")
    return Timestamp(stamp["time"], stamp["increment"])


class PinnedMongoSource:
    backend = "mongodb"

    def __init__(self, database, session, *, max_seconds, max_time_ms, batch_size, barrier_time=None, barrier=None,
                 collection_allowlist=None, allow_local_plaintext=False):
        self.name = validate_name(database.name)
        self._database, self._session = database, session
        self._max_time_ms, self._batch_size = max_time_ms, batch_size
        self._deadline = time.monotonic() + max_seconds
        self._started = datetime.now(timezone.utc)
        self._expires = self._started + timedelta(seconds=max_seconds)
        self._stamp, self._closed = None, False
        self._barrier_time = barrier_time
        self._majority_barrier = deepcopy(barrier)
        self._allow_local_plaintext = allow_local_plaintext
        self._archive_inventory = frozenset(collection_allowlist) if collection_allowlist is not None else None
        self._allowed = self._archive_inventory if self._archive_inventory is not None else frozenset(COLLECTION_DOMAINS)
        self._metadata_kinds = {}
        self._timestamp_confirmation = None
        self._view_id = uuid4().hex
        self._data_find_commands = self._metadata_reads = 0

    def ensure_active(self):
        _client_bounds(self._database, self._max_time_ms, allow_local_plaintext=self._allow_local_plaintext)
        if self._closed or time.monotonic() >= self._deadline or self._stamp is None:
            raise SnapshotUnavailable("The pinned MongoDB snapshot is unavailable or expired; it will not be advanced.")

    @property
    def view_identity(self):
        self.ensure_active()
        return {"view_id": self._view_id,
                "at_cluster_time": {"time": self._stamp.time, "increment": self._stamp.inc}}

    @property
    def evidence(self):
        self.ensure_active()
        return {**self.view_identity, "snapshot_session": True, "read_concern": "snapshot",
            "server_selected_timestamp": self._barrier_time is None,
            "writer_barrier_cluster_time": ({"time": self._barrier_time.time, "increment": self._barrier_time.inc}
                                            if self._barrier_time is not None else None),
            "majority_write_barrier": deepcopy(self._majority_barrier),
            "source_transport": _source_transport(self._database, allow_local_plaintext=self._allow_local_plaintext),
            "timestamp_confirmation": self._timestamp_confirmation,
            "subsequent_data_finds_use_same_explicit_timestamp": True,
            "subsequent_data_find_commands": self._data_find_commands, "metadata_reads": self._metadata_reads,
            "inventory_scope": "explicit_archive_inventory" if self._archive_inventory is not None else "personal_registry",
            "allowlist_inventory_fingerprint": hashlib.sha256(json.dumps(sorted(self._allowed), separators=(",", ":")).encode()).hexdigest(),
            "metadata_outside_snapshot": True, "application_background_and_ddl_barrier_required": True,
            "socket_timeout_seconds": self._database.client.options.pool_options.socket_timeout,
            "native_ttl_monitor_stopped": False, "started_at": self._started.isoformat(),
            "valid_until": self._expires.isoformat(), "credentials_or_record_values_included": False}

    async def _pin(self):
        if self._stamp is not None or self._closed:
            raise SnapshotUnavailable("A snapshot view cannot be repinned.")
        if self._session.options.snapshot is not True or self._session.options.causal_consistency:
            raise SnapshotUnavailable("A noncausal snapshot-enabled session is required.")
        # MongoDB requires _id on every record. This pins a read without
        # transferring a customer's record or relying on a local clock.
        try:
            self._metadata_reads += 1
            metadata = await asyncio.wait_for(self._database.list_collections(nameOnly=True), timeout=self._max_time_ms / 1000)
            catalog = []
            try:
                async for spec in metadata:
                    catalog.append((spec["name"], spec["type"]))
                    if len(catalog) > len(self._allowed):
                        raise SnapshotUnavailable("The source collection inventory exceeds its reviewed allowlist.")
            finally:
                await metadata.close()
            names = sorted(name for name, _ in catalog)
            if (not names or set(names) - self._allowed
                    or (self._archive_inventory is not None and set(names) != self._archive_inventory)):
                raise SnapshotUnavailable("A pinned source requires an existing, fully classified collection inventory.")
            physical = sorted(name for name, kind in catalog if kind == "collection")
            if not physical:
                raise SnapshotUnavailable("A pinned source requires a physical collection for its content-free barrier read.")
            pin_collection = physical[0]
            command = {"find": pin_collection, "filter": {"_id": {"$exists": False}}, "limit": 1, "batchSize": 1,
                       "readConcern": {"level": "snapshot"}, "maxTimeMS": self._max_time_ms}
            if self._barrier_time is not None:
                command["readConcern"]["atClusterTime"] = self._barrier_time
            response = await asyncio.wait_for(self._database.command(command, session=self._session, codec_options=RAW),
                                              timeout=self._max_time_ms / 1000)
            cursor = response.get("cursor", {})
            stamp = cursor.get("atClusterTime") or response.get("atClusterTime")
            if stamp is None and self._barrier_time is not None:
                # The successful command enforces this explicit server-supplied
                # cutoff even when MongoDB omits an echo of atClusterTime.
                stamp = self._barrier_time
                self._timestamp_confirmation = "server_accepted_explicit_readConcern_atClusterTime"
            else:
                self._timestamp_confirmation = "returned_atClusterTime"
            if not isinstance(stamp, Timestamp) or (self._barrier_time is not None and stamp != self._barrier_time):
                raise SnapshotUnavailable("The server did not provide a snapshot timestamp.")
            self._stamp = stamp
            if cursor.get("id"):
                await self._kill(pin_collection, cursor["id"])
        except SnapshotUnavailable:
            raise
        except Exception:
            raise SnapshotUnavailable("The server could not establish a supported pinned snapshot.") from None

    def __getitem__(self, name):
        self.ensure_active()
        _collection_name(name)
        if name not in self._allowed:
            raise SnapshotUnavailable("An unclassified source collection is not available through this view.")
        return _SnapshotCollection(self, name)

    def get_collection(self, name, *, codec_options=None):
        if codec_options is not None and codec_options.document_class is not RawBSONDocument:
            raise SnapshotUnavailable("The snapshot source exposes exact raw BSON only.")
        return self[name]

    async def list_collection_names(self):
        self.ensure_active()
        self._metadata_reads += 1
        try:
            return await self._database.list_collection_names()
        except Exception:
            raise SnapshotUnavailable("Source collection metadata could not be read.") from None

    async def collection_metadata(self, name, *, max_bytes=2 * 1024 * 1024):
        """Exact archive-only catalog/index BSON; never activates a saved view."""
        self.ensure_active()
        _collection_name(name)
        if self._archive_inventory is None or name not in self._archive_inventory:
            raise SnapshotUnavailable("Raw archive metadata requires an explicit reviewed collection inventory.")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 16 * 1024 * 1024:
            raise SnapshotUnavailable("Archive metadata requires a bounded byte limit.")
        specs = await self._raw_metadata({"listCollections": 1, "filter": {"name": name}, "nameOnly": False},
                                         max_bytes=max_bytes, max_records=1)
        if len(specs) != 1:
            raise SnapshotUnavailable("The reviewed source collection metadata changed.")
        spec = BSON(specs[0]).decode()
        kind = spec.get("type")
        if kind == "collection" and not name.startswith("system.buckets.") and "timeseries" in spec.get("options", {}):
            kind = "timeseries"
        if kind not in {"collection", "view", "timeseries"}:
            raise SnapshotUnavailable("The source collection has an unsupported catalog type.")
        self._metadata_kinds[name] = kind
        indexes = []
        if kind == "collection":
            remaining = max_bytes - len(specs[0])
            if remaining < 1:
                raise SnapshotUnavailable("Raw archive metadata exceeds its reviewed byte bound.")
            indexes = await self._raw_metadata({"listIndexes": name}, max_bytes=remaining, max_records=256)
        return {"spec_bson": specs[0], "indexes_bson": indexes, "data_kind": kind,
                "metadata_outside_snapshot": True}

    async def _raw_metadata(self, command, *, max_bytes, max_records):
        """Motor convenience methods normalize metadata; raw commands do not."""
        session, identity, collection = None, 0, None
        result, total = [], 0
        try:
            session = await self._database.client.start_session(causal_consistency=False)
            self._metadata_reads += 1
            response = await asyncio.wait_for(self._database.command({**command, "cursor": {"batchSize": 128},
                "maxTimeMS": self._max_time_ms}, session=session, codec_options=RAW), timeout=self._max_time_ms / 1000)
            cursor = response["cursor"]
            prefix = self.name + "."
            if not cursor["ns"].startswith(prefix):
                raise SnapshotUnavailable("The metadata cursor namespace changed.")
            collection = cursor["ns"][len(prefix):]
            batch = cursor["firstBatch"]
            while True:
                self.ensure_active()
                identity = cursor["id"]
                for record in batch:
                    if not isinstance(record, RawBSONDocument):
                        raise SnapshotUnavailable("Exact raw metadata BSON is unavailable.")
                    total += len(record.raw)
                    if total > max_bytes or len(result) >= max_records:
                        raise SnapshotUnavailable("Raw archive metadata exceeds its reviewed bounds.")
                    result.append(record.raw)
                if not identity:
                    break
                self._metadata_reads += 1
                response = await asyncio.wait_for(self._database.command({"getMore": identity, "collection": collection,
                    "batchSize": 128}, session=session, codec_options=RAW), timeout=self._max_time_ms / 1000)
                cursor, batch = response["cursor"], response["cursor"]["nextBatch"]
            return result
        except SnapshotUnavailable:
            raise
        except Exception:
            raise SnapshotUnavailable("Exact source archive metadata could not be read.") from None
        finally:
            if session is not None:
                try:
                    if identity and collection:
                        await asyncio.wait_for(self._database.command({"killCursors": collection, "cursors": [identity]},
                                                                      session=session), timeout=2)
                except Exception:
                    pass
                finally:
                    try:
                        await asyncio.wait_for(session.end_session(), timeout=2)
                    except Exception:
                        pass

    async def _find(self, name):
        self.ensure_active()
        self._data_find_commands += 1
        command = {"find": name, "filter": {}, "batchSize": self._batch_size,
            "readConcern": {"level": "snapshot", "atClusterTime": self._stamp}, "maxTimeMS": self._max_time_ms}
        try:
            response = await asyncio.wait_for(self._database.command(command, session=self._session, codec_options=RAW),
                timeout=min(self._max_time_ms / 1000, max(0.001, self._deadline - time.monotonic())))
            cursor = response["cursor"]
            stamp = cursor.get("atClusterTime") or response.get("atClusterTime")
            if stamp is not None and stamp != self._stamp:
                raise SnapshotUnavailable("The server returned a different snapshot timestamp.")
            return cursor
        except SnapshotUnavailable:
            raise
        except Exception:
            raise SnapshotUnavailable("A pinned snapshot read failed; the view will not be advanced.") from None

    async def _more(self, name, identity):
        self.ensure_active()
        try:
            # Non-awaitData getMore prohibits maxTimeMS (including the automatic
            # field added by driver CSOT to a manual command). Bound the await;
            # find's server execution budget remains attached to the cursor.
            response = await asyncio.wait_for(self._database.command({"getMore": identity, "collection": name,
                "batchSize": self._batch_size}, session=self._session, codec_options=RAW),
                timeout=min(self._max_time_ms / 1000, max(0.001, self._deadline - time.monotonic())))
            return response["cursor"]
        except Exception:
            raise SnapshotUnavailable("A pinned snapshot cursor failed; the view will not be advanced.") from None

    async def _kill(self, name, identity):
        try:
            await asyncio.wait_for(self._database.command({"killCursors": name, "cursors": [identity]},
                                                          session=self._session), timeout=2)
        except Exception:
            pass  # Ending the session still releases its bounded server resources.


class _SnapshotCollection:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name

    async def index_information(self):
        self.owner.ensure_active()
        self.owner._metadata_reads += 1
        try:
            return await self.owner._database[self.name].index_information()
        except Exception:
            raise SnapshotUnavailable("Source index metadata could not be read.") from None

    async def options(self):
        self.owner.ensure_active()
        self.owner._metadata_reads += 1
        try:
            return await self.owner._database[self.name].options()
        except Exception:
            raise SnapshotUnavailable("Source collection options could not be read.") from None

    def find(self, query=None):
        if query not in (None, {}):
            raise SnapshotUnavailable("The migration snapshot exposes only complete collection reads.")
        if self.owner._archive_inventory is not None and self.owner._metadata_kinds.get(self.name) != "collection":
            raise SnapshotUnavailable("Archive data reads require inspected physical metadata; saved views are never materialized.")
        return _SnapshotCursor(self.owner, self.name)


class _SnapshotCursor:
    def __init__(self, owner, name):
        self.owner, self.name = owner, name
        self._identity, self._batch, self._started, self._closed = 0, [], False, False

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.owner.ensure_active()
        if self._closed:
            raise StopAsyncIteration
        if not self._started:
            cursor = await self.owner._find(self.name)
            self._identity, self._batch, self._started = cursor["id"], list(cursor["firstBatch"]), True
        while not self._batch and self._identity:
            cursor = await self.owner._more(self.name, self._identity)
            self._identity, self._batch = cursor["id"], list(cursor["nextBatch"])
        if not self._batch:
            raise StopAsyncIteration
        return self._batch.pop(0)

    async def to_list(self, length=None):
        result = []
        while length is None or len(result) < length:
            try:
                result.append(await self.__anext__())
            except StopAsyncIteration:
                break
        return result

    async def close(self):
        if self._identity:
            await self.owner._kill(self.name, self._identity)
        self._identity, self._batch, self._closed = 0, [], True


@asynccontextmanager
async def pinned_source(database, *, max_seconds=240, max_time_ms=20000, batch_size=128, barrier_time=None, barrier=None,
                        collection_allowlist=None, allow_local_plaintext=False):
    """Open one majority-committed view. Unsupported servers fail before copying."""
    if (type(max_seconds) is not int or not 1 <= max_seconds <= 600 or type(max_time_ms) is not int
            or not 1 <= max_time_ms <= 20000 or type(batch_size) is not int or not 1 <= batch_size <= 128):
        raise SnapshotUnavailable("Snapshot time and batch bounds are invalid.")
    if barrier_time is not None and not isinstance(barrier_time, Timestamp):
        raise SnapshotUnavailable("The writer barrier must use a server-supplied MongoDB timestamp.")
    if collection_allowlist is not None:
        if not isinstance(collection_allowlist, (list, tuple, set, frozenset)) or not 1 <= len(collection_allowlist) <= 1024:
            raise SnapshotUnavailable("An archive source requires an explicit bounded collection inventory.")
        if len(set(collection_allowlist)) != len(collection_allowlist):
            raise SnapshotUnavailable("An archive collection inventory contains duplicates.")
        for name in collection_allowlist:
            _collection_name(name)
    if barrier is not None:
        stamp = _validate_barrier(database, barrier, allow_local_plaintext=allow_local_plaintext)
        if barrier_time is not None and barrier_time != stamp:
            raise SnapshotUnavailable("The explicit timestamp differs from the majority writer barrier.")
        barrier_time = stamp
    _client_bounds(database, max_time_ms, allow_local_plaintext=allow_local_plaintext)
    session = None
    view = None
    try:
        try:
            session = await database.client.start_session(snapshot=True)
            view = PinnedMongoSource(database, session, max_seconds=max_seconds, max_time_ms=max_time_ms,
                                     batch_size=batch_size, barrier_time=barrier_time, barrier=barrier,
                                     collection_allowlist=collection_allowlist, allow_local_plaintext=allow_local_plaintext)
            await view._pin()
        except SnapshotUnavailable:
            raise
        except Exception:
            raise SnapshotUnavailable("The pinned snapshot session could not start.") from None
        yield view
    finally:
        if view is not None:
            view._closed = True
        if session is not None:
            try:
                await asyncio.wait_for(session.end_session(), timeout=2)
            except Exception:
                pass
