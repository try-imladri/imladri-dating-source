"""Bounded all-variable scans of eligible questionnaire answers."""
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

try:
    from . import questionnaire_report as qr, research, research_trends as trends
except ImportError:
    import questionnaire_report as qr
    import research
    import research_trends as trends


SCREEN_SIZE = 90
BLOCK_SIZE = 56
CHUNK_SIZE = 2048
TOP = 40


def measures(variables):
    return [(key, tag) for key, field in variables.items()
            for tag in ((None, *research.CONDITIONS) if field["domain"] == "intimacy" else (None,))]


async def possible(db, eligible, unit):
    # An upper bound is sufficient to avoid building plans below the publication threshold.
    minimum = trends.MIN_PAIRED
    enrollment = qr.ongoing_members(eligible)
    if unit != "member":
        # The unique membership index permits only one active pair per person in an environment.
        if await db.users.count_documents(enrollment, limit=2 * minimum) < 2 * minimum:
            return False
        return await db.couples_pairs.count_documents({"env": eligible["env"], "state": "active"}, limit=minimum) >= minimum
    current = await db.users.count_documents(enrollment, limit=minimum)
    if current >= minimum:
        return True
    previous = await db.research_responses.count_documents({"env": eligible["env"], "submitted": True,
                                                            "consent_version": research.CONSENT_VERSION}, limit=minimum - current)
    return current + previous >= minimum


def source_codes(db, eligible, quiz, state, unit, selection, ids):
    """Read just the selected categorical codes; no names, member IDs, answers in words or notes leave MongoDB."""
    axes = {f"v{i}": selection[i] for i in ids}
    if unit == "member":
        source = db.users
        pipeline = qr.member_seat(eligible, quiz, state[2], axes)
        if any(key == qr.AGE or key in state[1] for key, _ in axes.values()):
            pipeline.append({"$unionWith": {"coll": "research_responses",
                                            "pipeline": trends.legacy_seat(eligible, axes, state[1], state[0])}})
        seats = {"a": "seat"}
    else:
        source = db.couples_pairs
        pipeline = qr.couple_seats(eligible, quiz, state[2], axes)
        seats = {"a": "a", "b": "b"}
    pipeline.append({"$project": {"_id": 0, **{name: ["$" + path + "." + slot for slot in axes]
                                               for name, path in seats.items()}}})
    return source.aggregate(pipeline, batchSize=128, maxTimeMS=55000)


def valid(code, size):
    return type(code) is int and 0 <= code < size


async def margins(stream, ids, sizes, unit):
    sides = 1 if unit in ("member", "couple") else 2
    totals = [[0] * len(ids) for _ in range(sides)]
    categories = [[Counter() for _ in ids] for _ in range(sides)]
    async for row in stream:
        a, b = row["a"], row.get("b")
        for pos, size in enumerate(sizes):
            if unit == "couple":
                codes = {c for c in (a[pos], b[pos]) if valid(c, size)}
                if codes:
                    totals[0][pos] += 1
                    categories[0][pos].update(codes)
            else:
                seats = (a,) if unit == "member" else ((a, b) if unit == "couple_inviter" else (b, a))
                for side, seat in enumerate(seats):
                    c = seat[pos]
                    if valid(c, size):
                        totals[side][pos] += 1
                        categories[side][pos][c] += 1
    # Both rules are necessary for *any* released joint table. Rare remaining categories may disappear
    # from a complete-case comparison, so never prune a variable solely for having a rare category.
    keep = [[key for pos, key in enumerate(ids) if totals[s][pos] >= trends.MIN_PAIRED and
             sum(n >= trends.MIN_CELL for n in categories[s][pos].values()) >= 2] for s in range(sides)]
    return {"left": keep[0], "right": keep[-1]}


def block_layout(screening, unit):
    left = sorted(i for page in screening.values() for i in page["left"])
    right = sorted(i for page in screening.values() for i in page["right"])
    xs = [left[i:i + BLOCK_SIZE] for i in range(0, len(left), BLOCK_SIZE)]
    ys = [right[i:i + BLOCK_SIZE] for i in range(0, len(right), BLOCK_SIZE)]
    symmetric = unit in ("member", "couple")
    return [(a, b) for i, a in enumerate(xs) for j, b in enumerate(ys) if not symmetric or i <= j]


def valid_pair(left, right, selection, unit):
    return (unit not in ("member", "couple") or left <= right) and trends.comparable(selection[left], selection[right], unit)


def masks(rows, seat, sizes):
    # One temporary bit per observational unit; masks are discarded after this chunk.
    buckets = [{} for _ in sizes]
    present = [0] * len(sizes)
    for i, row in enumerate(rows):
        bit = 1 << i
        for col, code in enumerate(row[seat]):
            if valid(code, sizes[col]):
                bucket = buckets[col]
                bucket[code] = bucket.get(code, 0) | bit
                present[col] |= bit
    return buckets, present


def add_chunk(rows, sizes, pairs, unit, tables):
    a, has_a = masks(rows, "a", sizes)
    if unit == "member":
        b, has_b = a, has_a
    else:
        b, has_b = masks(rows, "b", sizes)
        if unit == "couple_invitee":
            a, b = b, a
    for k, (x, y) in enumerate(pairs):
        table = tables[k]
        if unit == "couple":
            # A couple with two complete orientations apportions half its weight to each.
            both = has_a[x] & has_b[y] & has_b[x] & has_a[y]
            dirs = ((a[x], b[y]), (b[x], a[y]))
        else:
            both = 0
            dirs = ((a[x], b[y]),)
        for left, right in dirs:
            for i, bits in left.items():
                for j, others in right.items():
                    overlap = bits & others
                    if overlap:
                        table[i, j] += 2 * overlap.bit_count() - (overlap & both).bit_count()


def ranking(item):
    return (-item["cramers_v"], -item["sample_size"], item["indices"])


async def score(db, eligible, quiz, state, unit, selection, pairs):
    if not pairs:
        return [], 0
    ids = sorted({i for pair in pairs for i in pair})
    positions = {key: pos for pos, key in enumerate(ids)}
    sizes = [len(trends.options(state[2], *selection[i])) for i in ids]
    local_pairs = [(positions[i], positions[j]) for i, j in pairs]
    stream = source_codes(db, eligible, quiz, state, unit, selection, ids)
    tables = [Counter() for _ in pairs]

    while True:
        rows = await stream.to_list(CHUNK_SIZE)
        if not rows:
            break
        await asyncio.to_thread(add_chunk, rows, sizes, local_pairs, unit, tables)
    results = []
    for pair, (ix, iy), sparse in zip(pairs, local_pairs, tables):
        if not sparse:
            continue
        dense = [[0] * sizes[iy] for _ in range(sizes[ix])]
        for (i, j), doubled in sparse.items():
            dense[i][j] = doubled / 2
        summary = trends.summarize(dense)
        if summary["available"]:
            x, xt = selection[pair[0]]
            y, yt = selection[pair[1]]
            results.append({"indices": list(pair), "x": x, "x_condition": xt, "y": y, "y_condition": yt,
                            "sample_size": summary["sample_size"], "cramers_v": summary["cramers_v"]})
    return sorted(results, key=ranking)[:TOP], len(results)


async def page(db, eligible, quiz, unit="member", plan=None, cursor=0, review=None):
    if unit not in qr.UNITS or cursor < 0:
        raise HTTPException(422, "Choose a valid research scan.")
    if plan is None:
        if cursor or review is not None:
            raise HTTPException(422, "Start a fresh catalog search.")
        if not await possible(db, eligible, unit):
            return {"phase": "done", "matches": [], "next_cursor": None, "tested": 0}
    state = await trends.catalog(db, quiz)
    selection = measures(state[2])
    fingerprint = research.fingerprint([state[0], [[key, value.get("signature") or value] for key, value in state[2].items()]])
    env = research.fingerprint(eligible["env"])
    ts = datetime.now(timezone.utc)
    expiry = ts + timedelta(hours=1)
    if plan is None:
        plan = uuid4().hex
        record = {"_id": plan, "env": env, "unit": unit, "catalog": fingerprint, "expires_at": expiry, "screening": {}}
        await db.questionnaire_scan_plans.insert_one(record)
    else:
        record = await db.questionnaire_scan_plans.find_one({"_id": plan, "env": env, "unit": unit, "expires_at": {"$gt": ts}})
        if not record or record["catalog"] != fingerprint:
            raise HTTPException(409, "This search expired or the questionnaires changed. Start a new search.")
    screening = record["screening"]
    pages = (len(selection) + SCREEN_SIZE - 1) // SCREEN_SIZE
    if cursor < pages:
        if review is not None or (cursor and str(cursor - 1) not in screening):
            raise HTTPException(422, "Continue the questionnaire search in order.")
        key = str(cursor)
        if key not in screening:
            ids = list(range(cursor * SCREEN_SIZE, min((cursor + 1) * SCREEN_SIZE, len(selection))))
            sizes = [len(trends.options(state[2], *selection[i])) for i in ids]
            stream = source_codes(db, eligible, quiz, state, unit, selection, ids)
            screening[key] = await margins(stream, ids, sizes, unit)
            await db.questionnaire_scan_plans.update_one({"_id": plan}, {"$set": {"screening." + key: screening[key], "expires_at": expiry}})
        if cursor + 1 == pages and not block_layout(screening, unit):
            return {"phase": "done", "matches": [], "next_cursor": None, "tested": 0}
        return {"phase": "screening", "plan": plan, "completed": cursor + 1, "total": pages, "next_cursor": cursor + 1}
    if len(screening) != pages:
        raise HTTPException(422, "Finish screening the catalog before comparing answers.")
    blocks = block_layout(screening, unit)
    index = cursor - pages
    if review is not None:
        allowed_left = {i for v in screening.values() for i in v["left"]}
        allowed_right = {i for v in screening.values() for i in v["right"]}
        if index != len(blocks) or len(review) > TOP or any(x not in allowed_left or y not in allowed_right or
                                                not valid_pair(x, y, selection, unit) for x, y in review):
            raise HTTPException(422, "Choose valid search results for the final check.")
        results, _ = await score(db, eligible, quiz, state, unit, selection, sorted(set(tuple(p) for p in review)))
        return {"phase": "done", "matches": results, "next_cursor": None}
    if not 0 <= index < len(blocks):
        raise HTTPException(422, "Choose a valid comparison page.")
    left, right = blocks[index]
    pairs = [(x, y) for x in left for y in right if valid_pair(x, y, selection, unit)]
    results, released = await score(db, eligible, quiz, state, unit, selection, pairs)
    await db.questionnaire_scan_plans.update_one({"_id": plan}, {"$set": {"expires_at": expiry}})
    return {"phase": "comparing", "plan": plan, "matches": results, "released": released, "tested": len(pairs),
            "completed": index + 1, "total": len(blocks), "next_cursor": cursor + 1, "verify": index + 1 == len(blocks)}
