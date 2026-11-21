"""Archive inspection is a fixed read-only operator branch, never a retry."""
import asyncio
from uuid import uuid4

import pytest

from backend import postgres_operator as worker
from backend.tests.test_postgres_operator_control import environment, request


def test_archive_diagnostic_requires_exact_archive_alias_and_distinct_failed_archive_link():
    env = environment()
    failed = str(uuid4())
    body = request("inspect_archive_state", source="recovery_staging", failed_archive_id=failed)
    assert worker.validate_request(body, env)["failed_archive_id"] == failed
    for change in ({"source": "personal_preview"}, {"source": "market_live"}, {"failed_archive_id": None},
                   {"operation_id": failed}, {"fence": {}}, {"failed_copy_id": failed},
                   {"command": "archive_legacy"}):
        with pytest.raises(worker.OperatorBlocked): worker.validate_request({**body, **change}, env)
    without_link = {key: value for key, value in body.items() if key != "failed_archive_id"}
    with pytest.raises(worker.OperatorBlocked): worker.validate_request(without_link, env)


@pytest.mark.parametrize("command,link_key,source", [
    ("inspect_archive_state", "failed_archive_id", "recovery_staging"),
    ("inspect_copy_state", "failed_copy_id", "personal_preview")])
def test_readonly_diagnostic_never_reserves_or_finishes_an_intent(monkeypatch, command, link_key, source):
    import pg8000.dbapi
    closed = []
    class Connection:
        def close(self): closed.append(True)
    class NoWritesJournal:
        def reserve(self, *_): pytest.fail("Read-only diagnostic reserved an intent")
        def finish(self, *_args, **_kwargs): pytest.fail("Read-only diagnostic finished or reset an intent")
    monkeypatch.setattr(pg8000.dbapi, "connect", lambda **_: Connection())
    seen = []
    async def execute(body, config, journal):
        seen.append(body[link_key])
        return {"diagnostic_only": True, "prior_intent_modified": False, "target_application_write_operations": 0}
    monkeypatch.setattr(worker, "execute", execute)
    config = {"configuration_sha256": "c" * 64, "owner_options": {}}
    async def run():
        body = request(command, source=source, **{link_key: str(uuid4())})
        result = await worker.run({"action": "execute", "request": body}, environment(),
            config_loader=lambda _: config, journal_factory=lambda _: NoWritesJournal())
        assert result["status"] == "completed" and result["read_only_diagnostic"] is True
        assert result["diagnostic_result_durable_in_operator_journal"] is False
        assert result["needs_manual_review"] is True and seen == [body[link_key]]
    asyncio.run(run())
    assert closed == [True]
