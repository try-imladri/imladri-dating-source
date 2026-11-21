"""One fresh /me discovery cohort; later consent/funding checks stay uncached.

This object is created and consumed by one request. Only these exact discovery
queries/projections are served from its snapshot. Every other read is delegated.
"""
from copy import deepcopy


class AccountReadContext:
    def __init__(self, db, member, pair_rows, controls):
        self.db = db
        uid, env = member["user_id"], member["env"]
        base = {"env": env, "state": "active", "members": uid}
        self.reads = [
            ("couples_pairs", base, {"_id": 1, "members": 1, "inviter": 1, "invitee": 1, "sponsor": 1}, pair_rows["workspace"]),
            ("couples_pairs", {**base, "$or": [{"inviter": uid}, {"invitee": uid}]}, {"_id": 1}, pair_rows["research"]),
            ("couples_pairs", {**base, "benefit_members": uid},
             {"inviter": 1, "invitee": 1, "sponsor": 1, "annual_opens_at": 1, "annual_expires_at": 1, "qualifying_subscription": 1}, pair_rows["benefit"]),
            ("intro_controls", {"_id": "global"}, None, controls),
        ]

    @classmethod
    async def read(cls, db, member):
        uid, env = member["user_id"], member["env"]
        # Narrow indexed owner match. No private cross-collection lookup: the
        # only lookup reads the small public introduction-control record.
        rows = await db.couples_pairs.aggregate([{"$match": {"env": env, "members": uid}}, {"$facet": {
            "workspace": [{"$match": {"state": "active"}}, {"$project": {"_id": 1, "members": 1, "inviter": 1, "invitee": 1, "sponsor": 1}}, {"$limit": 1}],
            "research": [{"$match": {"state": "active", "$or": [{"inviter": uid}, {"invitee": uid}]}}, {"$project": {"_id": 1}}, {"$limit": 1}],
            "benefit": [{"$match": {"state": "active", "benefit_members": uid}}, {"$project": {
                "inviter": 1, "invitee": 1, "sponsor": 1, "annual_opens_at": 1, "annual_expires_at": 1, "qualifying_subscription": 1}}, {"$limit": 1}],
        }}, {"$lookup": {"from": "intro_controls", "pipeline": [{"$match": {"_id": "global"}},
            {"$project": {"_id": 1, "paused": 1}}, {"$limit": 1}], "as": "controls"}}]).to_list(1)
        result = rows[0]
        return cls(db, member, result, result["controls"])

    def __getattr__(self, name):
        if name not in ("couples_pairs", "intro_controls"):
            return getattr(self.db, name)
        context = self
        collection = getattr(self.db, name)
        class DiscoveryReads:
            async def find_one(self, query=None, projection=None, **options):
                if not options:
                    for selected, expected, fields, rows in context.reads:
                        if selected == name and query == expected and projection == fields:
                            return deepcopy(rows[0]) if rows else None
                # In particular: pair_snapshot, active_pair, adult_members and
                # their current partner/sharing/funding checks always get here.
                return await collection.find_one(query, projection, **options)
            def __getattr__(self, operation):
                return getattr(collection, operation)
        return DiscoveryReads()
