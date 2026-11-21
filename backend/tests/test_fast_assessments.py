"""Fresh combined assessment reads and parallel account reads; no external providers."""
import asyncio
from backend.tests.test_individual_answer_history import run_case


def test_assessment_bootstrap_matches_existing_reads_and_keeps_owner_and_access_guards(monkeypatch):
    async def scenario(db, call, quiz, server):
        original=await call("GET","/me/about")
        catalog=await call("GET","/quiz")
        result=await call("GET","/me/assessments",raw=True)
        assert result.headers["cache-control"]=="private, no-store"
        assert result.json()=={"owner_id":"owner","quiz":catalog,**original}
        assert "prompts" not in result.json()["quiz"]
        await call("GET","/me/assessments",uid="unpaid",status=402)
        await call("GET","/me/assessments",uid=None,status=401)
        await db.users.update_one({"user_id":"peer"},{"$set":{"about.describe":"private peer"}})
        other=await call("GET","/me/assessments",uid="peer")
        assert other["owner_id"]=="peer" and other["about"]["describe"]=="private peer"
        assert "private peer" not in str(await call("GET","/me/assessments"))
    run_case(monkeypatch,scenario)


def test_account_bootstrap_runs_independent_read_guards_concurrently(monkeypatch):
    async def scenario(db, call, quiz, server):
        started=set();all_started=asyncio.Event()
        def reader(name,value):
            async def read(*args):
                started.add(name)
                if len(started)==4:all_started.set()
                await asyncio.wait_for(all_started.wait(),1)
                return value
            return read
        monkeypatch.setattr(server.workspace_access,"snapshot",reader("workspace",{"mode":"individual"}))
        monkeypatch.setattr(server.main_consent,"snapshot",reader("consent",{"version":"fixture"}))
        monkeypatch.setattr(server.research_rows,"preferences",reader("exports",{}))
        monkeypatch.setattr(server.intro_access,"status",reader("intro",{}))
        member=await db.users.find_one({"user_id":"owner"})
        result=await server.me_out(member)
        assert len(started)==4 and result["user_id"]=="owner"
        assert result["workspace_access"]=={"mode":"individual"}
    run_case(monkeypatch,scenario)
