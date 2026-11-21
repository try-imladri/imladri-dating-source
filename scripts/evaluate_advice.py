"""Generate blinded comparisons of member-facing advice using fictional accounts and a local database."""
import argparse
import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

DEFAULT_CASES = Path(__file__).with_name("advice_cases.json")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def generate(args, cases):
    from fastapi import BackgroundTasks, Request

    if urlsplit(args.mongo).hostname not in {"127.0.0.1", "::1", "localhost"} or not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("Supply a loopback MongoDB URI and the model key through DEEPSEEK_API_KEY.")
    database = "advice_eval_" + uuid4().hex
    os.environ.update(MONGO_URL=args.mongo, DB_NAME=database, PYTHON_DOTENV_DISABLED="1",
                      CLERK_PUBLISHABLE_KEY="pk_test_advice_eval", PERSONAL_BILLING_MODE="disabled")
    sys.path.insert(0, str(args.source.resolve()))
    from backend import server

    limit = asyncio.Semaphore(4)

    async def run(case):
        async with limit:
            uid = case["id"]
            pid = uid + "-person"
            timestamp = int(time.time())
            member = {"user_id": uid, "role": "member", "tier": "paid", "stripe_subscription_id": "fictional",
                      "subscription_status": "active", "subscription_period_start": timestamp - 3600,
                      "subscription_period_end": timestamp + 3600, "onboarding_step": 5}
            stage = {"partner": "established", "ex": "fading", "talking": "early"}.get(case["relationship"], "good")
            person = {"id": pid, "user_id": uid, "rel_type": case["relationship"], "nickname": case["name"],
                      "goal": case["goal"], "stage": stage}
            await server.db.users.insert_one(dict(member))
            await server.db.people.insert_one(dict(person))
            lines = [{"speaker": role, "text": words} for role, words in case.get("lines", [])]
            feature, question = case["feature"], case["ask"]
            if feature == "read":
                value = await server.run_exchange(person, member, lines, question, stage, person["goal"],
                                                  BackgroundTasks(), uid + "-read")
                return value["response"]
            if feature == "plan":
                request = Request({"type": "http", "headers": []})
                value = await server.create_prep(pid, server.PrepIn(situation=question), request, member)
                return value["plan"]
            if feature == "group":
                other = {**person, "id": pid + "-other", "nickname": case["other"]}
                await server.db.people.insert_one(other)
                value = await server.generate_group_answer([pid, other["id"]], question, member, uid + "-group")
                return value["response"]
            if feature == "practice":
                await server.db.practice.insert_one({"id": uid, "user_id": uid, "person_id": pid,
                    "topic": question, "messages": [{"role": item["speaker"], "text": item["text"]} for item in lines]})
                await server.practice_to_profile(uid, member)
            elif feature == "profile":
                await server.db.exchanges.insert_one({"id": uid + "-read", "user_id": uid, "person_id": pid,
                    "question": question, "conversation": lines, "created_at": server.now(), "has_conversation": True})
                await server.scan_patterns(member)
            else:
                raise ValueError(feature)
            patterns = await server.list_patterns(member)
            return {"saved_observations": [p["text"] for p in patterns],
                    "evidence_used_in_future_advice": await server.member_pattern_evidence(uid, 30)}

    try:
        # Settle all requests before cleaning up, including when a provider call fails.
        results = await asyncio.gather(*(run(c) for c in cases), return_exceptions=True)
        failures = [(c["id"], type(result).__name__, getattr(result, "status_code", ""))
                    for c, result in zip(cases, results) if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError(f"Evaluation incomplete: {failures}")
        write_json(args.output, dict(zip((c["id"] for c in cases), results)))
    finally:
        await server.client.drop_database(database)
        server.client.close()


def judgement_view(case, answer):
    if case["feature"] in {"read", "group"}:
        fields = ("what_it_means", "best_move", "what_to_say", "what_not_to_do", "actions")
        return {key: answer[key] for key in fields if key in answer}
    return answer


def blind(args, cases):
    first, second = (json.loads(p.read_text(encoding="utf-8")) for p in (args.before, args.after))
    if set(first) != set(second) or set(first) != {c["id"] for c in cases}:
        raise ValueError("Both runs must cover every case.")
    deck, key = [], {}
    for case in cases:
        cid = case["id"]
        swap = secrets.randbelow(2)
        raw = (first[cid], second[cid])
        deck.append({"case": case, "A": judgement_view(case, raw[swap]), "B": judgement_view(case, raw[1-swap])})
        key[cid] = "AB"[1-swap]
    write_json(args.output, deck)
    write_json(args.key, key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="Call the configured model on the synthetic cases.")
    gen.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    gen.add_argument("--mongo", required=True)
    gen.add_argument("--output", type=Path, required=True)
    pair = commands.add_parser("blind", help="Shuffle old/new order separately for each case.")
    for command in (gen, pair):
        command.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    for arg in ("before", "after", "output", "key"):
        pair.add_argument("--" + arg, required=True, type=Path)
    opts = parser.parse_args()
    cases = json.loads(opts.cases.read_text(encoding="utf-8"))
    if len({case["id"] for case in cases}) != len(cases):
        parser.error("Each case must have a distinct ID.")
    if opts.command == "generate":
        asyncio.run(generate(opts, cases))
    else:
        blind(opts, cases)
