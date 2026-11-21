"""Real SQL/Mongo research parity on explicit synthetic current/history cohorts."""
import asyncio
from datetime import datetime, timezone
import os

import pytest

from backend import numeric_research, postgres_migration as migration, postgres_research as sql, questionnaire_report as qr, research_trends
from backend.quiz import default_quiz
from backend.tests.test_postgres_migration import with_database, event


@pytest.mark.skipif(not (os.getenv("IMLADRI_TEST_POSTGRES_DSN") and os.getenv("IMLADRI_TEST_MONGO_URL")),
                    reason="Requires isolated loopback MongoDB and PostgreSQL.")
def test_postgres_numeric_and_categorical_queries_match_current_mongo_member_and_couple_outputs():
    async def exercise(db, target, namespace):
        quiz = default_quiz()
        state = await research_trends.catalog(db, quiz)
        variables = state[2]
        x, y = [key for key, variable in variables.items() if variable["domain"] == "intimacy"][:2]
        signatures = {key: variables[key]["signature"] for key in (x, y)}
        members, pairs, current, history = [], [], [], []
        stamp = datetime.now(timezone.utc).isoformat()
        # 120 couples in four occupied cells. Every cell stays above the10-pair
        # floor after deliberate skips, changed editions and consent exclusions.
        for i in range(120):
            group = i // 30
            age = (22, 32, 42, 52)[group]
            height = (155, 175, 155, 175)[group]
            x_code, y_code = divmod(group, 2)
            uids = [f"person-{i}-a", f"person-{i}-b"]
            pairs.append({"_id": f"pair-{i}", "inviter": uids[0], "invitee": uids[1], "members": uids,
                          "state": "active", "env": "fixture"})
            for side, uid in enumerate(uids):
                profile = {"age": age + side, "height_cm": height + side}
                if i % 30 == 0 and side == 0:
                    profile.pop("age")
                if i % 10 == 0 and side == 1:
                    profile.pop("height_cm")
                member = {"_id": uid, "user_id": uid, "role": "member", "env": "fixture", "about": {"demographics": profile},
                          "couples_adult_at": stamp, "questionnaire_research_version": "all-answers-v1"}
                if i % 20 == 5 and side == 1:
                    member.pop("questionnaire_research_version")
                members.append(member)
                edition_x = "previous-edition" if i % 20 == 3 else signatures[x]
                value_y = None if i % 20 == 4 else y_code
                current.append({"_id": uid, "user_id": uid, "values": {x: x_code, y: value_y},
                                "signatures": {x: edition_x, y: signatures[y]}})
                demo = event("demo-" + uid, uid, domain="demographics")
                demo.update(source="users", question_id="demographics", after=profile, baseline=True, source_revision=1)
                history.append(demo)
                for key, code, edition in ((x, x_code, edition_x), (y, value_y, signatures[y])):
                    old = event("old-" + uid + key, uid, snapshot="demo-" + uid)
                    old.update(question_id=key, signature=edition, after={"choice": 1 if code == 0 else 0, "skipped": False},
                               source_revision=1, baseline=True)
                    new = event("new-" + uid + key, uid, snapshot="demo-" + uid)
                    new.update(question_id=key, signature=edition, after={"choice": code, "skipped": code is None}, source_revision=2)
                    history.extend([old, new])
        await db.users.insert_many(members)
        await db.couples_pairs.insert_many(pairs)
        await db.intimacy_answers.insert_many(current)
        await db.answer_history.insert_many(history)
        report = await migration.reconcile(db, target, namespace=namespace, environment="fixture")
        assert report["observed_parity"] and report["projections"]["projection_issues"] == 0
        eligible = {"env": "fixture"}
        numeric_axes = {"x": (qr.AGE, None), "y": (qr.HEIGHT, None)}
        categorical_axes = {"x": (x, None), "y": (y, None)}
        for unit in ("member", "couple", "couple_inviter", "couple_invitee"):
            mongo_numeric = await numeric_research.correlation(db, eligible, quiz, variables, numeric_axes, unit, 50, 10, 5)
            pg_numeric = sql.numeric_correlation(target, namespace, unit=unit)
            assert pg_numeric == mongo_numeric, (unit, pg_numeric, mongo_numeric)
            mongo_categorical = (await research_trends.comparisons(db, eligible, quiz, state, categorical_axes, unit))["y"]
            pg_categorical = sql.categorical_comparison(target, namespace, x=x, x_edition=signatures[x], x_size=5,
                                                       y=y, y_edition=signatures[y], y_size=5, unit=unit)
            assert {key: pg_categorical[key] for key in mongo_categorical} == mongo_categorical, (unit, pg_categorical, mongo_categorical)
        # The database engine must not bypass small-sample or empty/constant-cell gates.
        assert sql.numeric_correlation(target, namespace, minimum=1000) is None
        hidden = sql.categorical_comparison(target, namespace, x=x, x_edition="unanswered-edition", x_size=5,
                                           y=y, y_edition=signatures[y], y_size=5)
        assert hidden["available"] is False
    asyncio.run(with_database(exercise))
