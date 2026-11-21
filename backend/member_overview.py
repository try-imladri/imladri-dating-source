"""Fresh owner-scoped Overview reads, grouped by collection, without a cache."""
import asyncio

from . import trends


async def read(db, uid, dates):
    owner = {"user_id": uid}
    metrics = {"people_added", "person_reports", "reads", "outcomes", "game_plans", "plan_followups", "practice", "group_chats", "quick_advice"}
    activity = {}
    for metric, name, stamp, filters in trends.SAVED_RECORDS:
        if metric in metrics:
            activity.setdefault(name, {})[metric] = trends.daily_count_pipeline(metric, stamp, dates, owner, filters, users=True)

    async def facets(name, extra):
        rows = await db[name].aggregate([{"$match": owner}, {"$facet": {**extra, **activity[name]}}]).to_list(1)
        return rows[0]

    people_task = asyncio.create_task(facets("people", {"recent": [{"$project": {
        "_id": 0, "id": 1, "nickname": 1, "rel_type": 1, "archived": 1, "report.updated_at": 1, "created_at": 1}}]}))

    async def exchanges():
        people = await people_task
        eligible = {**owner, "person_id": {"$in": [person["id"] for person in people["recent"]]}}
        pending = {**eligible, "choice.label": {"$exists": True}, "outcome": {"$in": [None, ""]}}
        review = {**eligible, "memories_reviewed": {"$ne": True}, "proposed_memories.0": {"$exists": True}}
        fields = {"_id": 0, "id": 1, "person_id": 1, "created_at": 1, "choice.label": 1, "proposed_memories": 1}
        def reminders(criteria):
            return [{"$match": criteria}, {"$sort": {"created_at": -1, "id": -1}}, {"$limit": 5}, {"$project": fields}]
        return await facets("exchanges", {
            "recent": [{"$sort": {"created_at": -1}}, {"$limit": 500}, {"$project": {
                "_id": 0, "id": 1, "person_id": 1, "has_conversation": 1, "created_at": 1, "outcome": 1,
                "choice": 1, "outcome_at": 1, "memories_reviewed": 1, "proposed_memories": 1}}],
            "pending": reminders(pending), "review": reminders(review),
            "pending_count": [{"$match": pending}, {"$count": "n"}], "review_count": [{"$match": review}, {"$count": "n"}],
        })

    people, reads, preps, practice, groups, quick, unread = await asyncio.gather(
        people_task, exchanges(),
        facets("preps", {"recent": [{"$match": {"deleting": {"$ne": True}}}, {"$sort": {"created_at": -1}}, {"$limit": 300},
            {"$project": {"_id": 0, "person_id": 1, "situation": 1, "created_at": 1, "outcome_at": 1}}]}),
        facets("practice", {"recent": [{"$sort": {"created_at": -1}}, {"$limit": 20}, {"$project": {"person_id": 1, "created_at": 1}}]}),
        facets("group_chats", {"recent": [{"$sort": {"created_at": -1}}, {"$limit": 20}, {"$project": {"person_ids": 1, "created_at": 1}}]}),
        facets("quick_advice", {"recent": [{"$match": {"workspace": "individual", "deleted": {"$ne": True}}},
            {"$sort": {"created_at": -1}}, {"$limit": 20}, {"$project": {"id": 1, "created_at": 1}}]}),
        db.notifications.count_documents({**owner, "read": False}),
    )
    collections = {"people": people, "exchanges": reads, "preps": preps, "practice": practice, "group_chats": groups, "quick_advice": quick}
    return {"people": people["recent"], "exchanges": reads["recent"], "preps": preps["recent"],
        "practice": practice["recent"], "group_chats": groups["recent"], "quick_advice": quick["recent"],
        "counts": [[row for metric in activity[name] for row in values[metric]] for name, values in collections.items()],
        "pending": reads["pending"], "review": reads["review"],
        "pending_count": reads["pending_count"][0]["n"] if reads["pending_count"] else 0,
        "review_count": reads["review_count"][0]["n"] if reads["review_count"] else 0, "unread": unread}
