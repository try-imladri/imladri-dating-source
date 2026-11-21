"""Recovery-only archive diagnosis: exact archive SQL, always rolled back.

This is a new fenced diagnostic, never a retry or completion of an unknown
archive. Only the separate Mongo control database may retain a majority barrier.
No provisional archive receipt or source record leaves this module.
"""
import asyncio
import hashlib
import ipaddress
import re
import ssl

from . import postgres_archive as archive


SETUP_STAGES = frozenset({"validate", "target_before", "source_topology", "source_barrier",
    "source_inventory", "target_connect", "source_pin", "archive_core", "target_after", "prior_intent"})
REASONS = frozenset({"rehearsal_fixed_recovery_source_required", "rehearsal_unknown_archive_link_mismatch",
    "rehearsal_prior_unknown_changed", "rehearsal_separate_control_database_required",
    "rehearsal_verified_TLS_required", "rehearsal_owner_connection_required",
    "rehearsal_autocommit_forbidden", "rehearsal_transaction_control_forbidden",
    "rehearsal_SQL_lexical_boundary_invalid", "rehearsal_schema_must_be_absent",
    "rehearsal_rollback_or_final_commit_not_verified", "rehearsal_result_not_verified",
    "rehearsal_failed"})


class ArchiveRehearsalBlocked(RuntimeError):
    def __init__(self, reason, *, stage="validate", original=None, cleanup=None):
        self.reason = reason if reason in REASONS else "rehearsal_failed"
        self.stage = stage if stage in SETUP_STAGES else "validate"
        self.original = archive.safe_archive_error(original) if original is not None else None
        self.cleanup = cleanup or {}
        super().__init__(self.reason)


def safe_rehearsal_error(error):
    if not isinstance(error, ArchiveRehearsalBlocked):
        return {"reason_code": "rehearsal_failed", "stage": "validate"}
    result = {"reason_code": error.reason, "stage": error.stage, "diagnostic_only": True,
        "archive_committed": False, "target_commit_operations": 0,
        "source_application_write_operations": 0, "source_control_majority_metadata_write_possible": True,
        **error.cleanup}
    if error.original:
        result["archive_path_error"] = error.original
    return result


def _require(value, reason, *, stage="validate"):
    if not value:
        raise ArchiveRehearsalBlocked(reason, stage=stage)


def link_unknown(request, journal):
    """Validate before reserving any new diagnostic intent or accessing data."""
    from .postgres_operator import RECOVERY_SOURCES, digest
    logical = request.get("source")
    _require(logical in RECOVERY_SOURCES, "rehearsal_fixed_recovery_source_required")
    previous = journal.lookup(request.get("failed_archive_id"))
    _require(previous and previous["state"] == "unknown" and previous["command"] == "archive_legacy"
        and previous["source"] == logical and isinstance(previous.get("request_sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", previous["request_sha256"]),
        "rehearsal_unknown_archive_link_mismatch")
    return {"failed_archive_id": request["failed_archive_id"], "prior_request_sha256": previous["request_sha256"],
        "prior_state_sha256": digest(previous)}


def _visible_statements(sql):
    """Exclude SQL literals/identifiers/bodies before checking statement verbs."""
    _require(isinstance(sql, str), "rehearsal_SQL_lexical_boundary_invalid")
    visible, index = [], 0
    while index < len(sql):
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            index = len(sql) if end < 0 else end
            visible.append(" ")
        elif sql.startswith("/*", index):
            depth, index = 1, index + 2
            while index < len(sql) and depth:
                if sql.startswith("/*", index): depth, index = depth + 1, index + 2
                elif sql.startswith("*/", index): depth, index = depth - 1, index + 2
                else: index += 1
            _require(depth == 0, "rehearsal_SQL_lexical_boundary_invalid")
            visible.append(" ")
        elif sql[index] in ("'", '"'):
            quote, index, closed = sql[index], index + 1, False
            while index < len(sql):
                if sql[index] == quote:
                    if index + 1 < len(sql) and sql[index + 1] == quote:
                        index += 2
                        continue
                    index, closed = index + 1, True
                    break
                index += 1
            _require(closed, "rehearsal_SQL_lexical_boundary_invalid")
            visible.append(" ")
        elif sql[index] == "$" and (match := re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", sql[index:])):
            tag = match.group()
            end = sql.find(tag, index + len(tag))
            _require(end >= 0, "rehearsal_SQL_lexical_boundary_invalid")
            index = end + len(tag)
            visible.append(" ")
        else:
            visible.append(sql[index])
            index += 1
    return [row.strip().upper() for row in "".join(visible).split(";") if row.strip()]


class _RollbackCursor:
    __slots__ = ("_cursor", "_guard")

    def __init__(self, cursor, guard):
        self._cursor, self._guard = cursor, guard

    def execute(self, sql, args=()):
        self._guard._check()
        for statement in _visible_statements(sql):
            verb = statement.split()[0]
            _require(verb not in {"BEGIN", "START", "COMMIT", "END", "ABORT", "ROLLBACK",
                "SAVEPOINT", "RELEASE", "PREPARE", "RESET", "DISCARD"}
                and (verb != "SET" or re.match(r"^SET\s+LOCAL\b", statement)),
                "rehearsal_transaction_control_forbidden")
        self._guard.statement_sequence += 1
        try:
            self._cursor.execute(sql, args)
        except Exception as error:
            self._guard.record_sql_failure(error, sql)
            raise
        return self

    def fetchone(self): return self._cursor.fetchone()
    def fetchall(self): return self._cursor.fetchall()
    def close(self): return self._cursor.close()


class RollbackOnlyConnection:
    """The trusted archive core receives no real commit/autocommit interface."""
    __slots__ = ("_connection", "commit_interceptions", "rollback_completed", "closed",
        "statement_sequence", "sql_failure")

    def __init__(self, connection):
        _require(connection.autocommit is False, "rehearsal_autocommit_forbidden")
        self._connection = connection
        self.commit_interceptions, self.rollback_completed, self.closed = 0, False, False
        self.statement_sequence, self.sql_failure = 0, None

    @property
    def autocommit(self): return False

    def _check(self):
        _require(not self.closed and self._connection.autocommit is False,
            "rehearsal_autocommit_forbidden")
        _require(self.commit_interceptions == 0, "rehearsal_transaction_control_forbidden")

    def cursor(self):
        self._check()
        return _RollbackCursor(self._connection.cursor(), self)

    def record_sql_failure(self, error, template):
        """Only known driver identities and a strict SQLSTATE may leave SQL."""
        import pg8000.dbapi
        if self.sql_failure is not None:
            return
        known = {getattr(pg8000.dbapi, name): name for name in (
            "DatabaseError", "DataError", "IntegrityError", "InternalError",
            "OperationalError", "ProgrammingError", "NotSupportedError")}
        driver_class = known.get(type(error))
        if driver_class is None or not isinstance(error, pg8000.dbapi.DatabaseError):
            return
        payload = error.args[0] if error.args and type(error.args[0]) is dict else {}
        state = payload.get("C")
        self.sql_failure = {"driver_class": driver_class,
            "statement_sha256": hashlib.sha256(template.encode("utf8")).hexdigest(),
            "statement_sequence": self.statement_sequence,
            "sqlstate": state if type(state) is str and re.fullmatch(r"[A-Z0-9]{5}", state) else None}

    def commit(self):
        self._check()
        self._connection.rollback()
        self.rollback_completed = True
        self.commit_interceptions += 1

    def rollback(self):
        if not self.closed:
            self._connection.rollback()
            self.rollback_completed = True

    def close(self):
        if not self.closed:
            try:
                self.rollback()
            finally:
                self.closed = True
                self._connection.close()


def _verified_owner_options(options, *, allow_local_plaintext=False):
    context = options.get("ssl_context")
    verified = isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    try:
        loopback = ipaddress.ip_address(options.get("host", "")).is_loopback
    except ValueError:
        loopback = False
    _require(verified or (allow_local_plaintext is True and loopback and context is False),
        "rehearsal_verified_TLS_required")
    _require(isinstance(options.get("user"), str) and not options["user"].startswith("imladri_app_"),
        "rehearsal_owner_connection_required")


def _absent_owner_schema(options, logical, database):
    from .postgres_archive_diagnostics import _owner_state
    state = _owner_state(options, logical, database)
    _require(state.get("dedicated_database_owner") is True and state.get("transaction_read_only") is True,
        "rehearsal_owner_connection_required")
    _require(state.get("schema_assessment") == "absent" and state.get("schema_exists") is False,
        "rehearsal_schema_must_be_absent")
    return True


async def rehearse_archive(config, request, journal, *, archive_runner, allow_local_plaintext=False):
    """Call the unchanged archive core with its final COMMIT replaced by rollback."""
    import pg8000.dbapi
    from .postgres_operator import RECOVERY_SOURCES, digest
    link = link_unknown(request, journal)
    expected_link = config.get("_verified_archive_rehearsal_link", link)
    _require(link == expected_link, "rehearsal_prior_unknown_changed")
    logical = request["source"]
    source = config["sources"][logical]
    _require(source["database"] == RECOVERY_SOURCES[logical]
        and source.get("control_database") == "imladri_migration_control",
        "rehearsal_separate_control_database_required")
    options = config["owner_options"]
    _verified_owner_options(options, allow_local_plaintext=allow_local_plaintext)
    stage, guarded, result, primary_error = "target_before", None, None, None
    try:
        await asyncio.to_thread(_absent_owner_schema, options, logical, source["database"])
    except Exception as error:
        reason = error.reason if isinstance(error, ArchiveRehearsalBlocked) else "rehearsal_failed"
        raise ArchiveRehearsalBlocked(reason, stage="target_before", original=error) from None

    def progress(value):
        nonlocal stage
        _require(value in SETUP_STAGES, "rehearsal_failed")
        stage = value

    def connect(**kwargs):
        nonlocal guarded
        _require(guarded is None, "rehearsal_transaction_control_forbidden")
        connection = pg8000.dbapi.connect(**kwargs)
        try:
            guarded = RollbackOnlyConnection(connection)
        except BaseException:
            connection.close()
            raise
        return guarded

    try:
        result = await archive_runner(config, request, _connection_factory=connect, _stage=progress)
    except BaseException as error:
        primary_error = error
    finally:
        if guarded is not None:
            try:
                guarded.close()
            except BaseException as error:
                if primary_error is None: primary_error = error
        cleanup = {"target_schema_absent_independently_verified": False, "prior_unknown_unchanged": False}
        if primary_error is not None and guarded is not None and guarded.sql_failure is not None:
            cleanup["sql_failure"] = dict(guarded.sql_failure)
        try:
            await asyncio.to_thread(_absent_owner_schema, options, logical, source["database"])
            cleanup["target_schema_absent_independently_verified"] = True
            current = journal.lookup(request["failed_archive_id"])
            _require(current is not None and digest(current) == link["prior_state_sha256"],
                "rehearsal_prior_unknown_changed", stage="prior_intent")
            cleanup["prior_unknown_unchanged"] = True
        except BaseException as error:
            if primary_error is None:
                primary_error, stage = error, "target_after"
    if isinstance(primary_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        raise primary_error
    if primary_error is not None:
        reason = primary_error.reason if isinstance(primary_error, ArchiveRehearsalBlocked) else "rehearsal_failed"
        raise ArchiveRehearsalBlocked(reason, stage=stage, original=primary_error, cleanup=cleanup) from None
    _require(guarded is not None and guarded.commit_interceptions == 1 and guarded.rollback_completed and guarded.closed,
        "rehearsal_rollback_or_final_commit_not_verified", stage="target_after")
    receipt = result.get("receipt") if isinstance(result, dict) else None
    _require(isinstance(receipt, dict) and receipt.get("status") == "verified_legacy_archive"
        and receipt.get("source_alias") == logical and receipt.get("source_database") == source["database"],
        "rehearsal_result_not_verified", stage="archive_core")
    counts = {key: receipt.get(key) for key in ("physical_records", "collection_count", "logical_target_payload_bytes")}
    _require(all(type(value) is int and value >= 0 for value in counts.values())
        and isinstance(receipt.get("manifest_sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", receipt["manifest_sha256"]),
        "rehearsal_result_not_verified", stage="archive_core")
    return {"diagnostic_only": True, "diagnostic_status": "rollback_rehearsal_validated",
        "source_alias": logical, "source_database": source["database"], **counts,
        "manifest_sha256": receipt["manifest_sha256"], "linked_failed_archive_id": link["failed_archive_id"],
        "source_control_majority_metadata_write": True, "source_application_write_operations": 0,
        "archive_committed": False, "archive_receipt_persisted": False, "target_commit_operations": 0,
        "final_commit_intercepted_and_rolled_back": True, **cleanup}
