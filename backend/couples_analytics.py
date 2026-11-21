"""Adult eligibility, invitation milestones and aggregate Couples reports."""
from datetime import date, datetime, time, timedelta, timezone
from statistics import median

from fastapi import HTTPException

try:
    from .couples_catalog import CARDS
    from .free_plan import complimentary_access, quota_period
    from .trends import disclosure, eligible_members, MINIMUM_RESPONDENTS
except ImportError:
    from couples_catalog import CARDS
    from free_plan import complimentary_access, quota_period
    from trends import disclosure, eligible_members, MINIMUM_RESPONDENTS


def underage(member):
    age = ((member.get("about") or {}).get("demographics") or {}).get("age")
    return age is not None and str(age).strip().isdecimal() and int(age) < 18


def is_adult(member):
    return bool(member and not member.get("closing") and member.get("couples_adult_at") and not underage(member))


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, (str, datetime)):
        return None
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def scheduled_review_start(value):
    if isinstance(value, str):
        try:
            return datetime.combine(date.fromisoformat(value), time.min, timezone.utc)
        except ValueError:
            pass
    return None


async def observe_offer(db, member):
    await db.users.update_one({"user_id": member["user_id"], "closing": {"$ne": True},
                               "couples_first_offer_at": None}, {"$set": {"couples_first_offer_at": utcnow().isoformat()}})


def fraction(numerator, denominator):
    return {"count": numerator, "eligible": denominator}


def first_joint_checkin(stats):
    if "first_both_checked_in_at" in stats:
        joint = timestamp(stats["first_both_checked_in_at"])
        return joint.isoformat() if joint else None
    # Older pairs retained only the first shared check-in timestamp per side.
    reviews = stats.get("first_checkins") or {}
    first, second = (timestamp(reviews.get(side)) for side in ("a", "b"))
    if first and second:
        return max(first, second).isoformat()
    return None


def shared_activities_projection():
    # Compute only the feature names inside MongoDB. The report never receives a
    # thread key, an answer, a learning sentence or an individual check-in choice here.
    mutual_threads = {"$filter": {
        "input": {"$objectToArray": {"$ifNull": ["$journey.threads", {}]}}, "as": "thread",
        "cond": {"$and": [{"$gt": [{"$size": {"$ifNull": [f"$$thread.v.shares.{side}", []]}}, 0]}
                          for side in ("a", "b")]},
    }}
    approved = {"$anyElementTrue": [{"$map": {
        "input": {"$ifNull": ["$$this.v.agreements", []]}, "as": "agreement",
        "in": {"$and": [
            {"$eq": ["$$agreement.checkins.a.shared", True]}, {"$eq": ["$$agreement.checkins.b.shared", True]},
            {"$setIsSubset": [["a", "b"], {"$ifNull": ["$$agreement.pin.accepted", []]}]},
        ]},
    }}]}
    return {"$cond": [{"$eq": ["$state", "active"]}, {"$reduce": {
        "input": mutual_threads, "initialValue": [],
        "in": {"$setUnion": ["$$value",
                             {"$cond": [{"$in": ["$$this.v.kind", ["prompt", "moment", "positive"]]}, ["$$this.v.kind"], []]},
                             {"$cond": [approved, ["learning"], []]}]},
    }}, []]}


async def paid_partner_memberships(db, env, eligible, start, current):
    dates = {"$gte": start.isoformat(), "$lte": current.isoformat()}
    # Initial checkout opens the guest's Individual account. Later $10 renewals do not open another offer.
    rows = db.payment_transactions.aggregate([
        {"$match": {"env": {"$in": [env, None]}, "currency": "usd", "payment_status": "paid", "$or": [
            {"plan_offer": "partner_monthly", "kind": "purchase", "fulfilled_at": dates},
            {"plan_offer": "couple_annual", "kind": "renewal", "created_at": dates},
        ]}},
        {"$group": {"_id": {"uid": "$user_id", "offer": "$plan_offer"}}},
        *eligible_members({**eligible, "closing": {"$ne": True}}, "_id.uid"),
        {"$group": {"_id": "$_id.offer", "accounts": {"$sum": 1}}},
    ])
    paid = {row["_id"]: row["accounts"] async for row in rows}
    return {"partner_upgrades": paid.get("partner_monthly", 0), "annual_payments": paid.get("couple_annual", 0)}


async def report(db, env, eligible, days, current=None):
    if days not in (7, 30, 90):
        raise HTTPException(422, "Choose a 7, 30 or 90 day cohort.")
    current = current or utcnow()
    start = datetime.combine(current.date() - timedelta(days=days - 1), time.min, timezone.utc)
    def in_window(at):
        return at is not None and start <= at <= current

    # Never load private drafts, shared wording, contact details or intimate answers.
    members = {}
    participating_adults = set()
    adult_members = {}
    review_since = {}
    funded_sponsors = set()
    annual_authorizations = 0
    offered = started = 0
    projection = {"_id": 0, "user_id": 1, "couples_first_offer_at": 1, "couples_first_draft_at": 1,
                  "couples_adult_at": 1, "about.demographics.age": 1, "couples_preferences.analytics_opt_in": 1,
                  "couples_preferences.review_return_opt_in": 1, "couples_preferences.review_return_since": 1}
    projection.update(dict.fromkeys(("tier", "stripe_subscription_id", "subscription_status", "subscription_period_start",
                                     "subscription_period_end", "plan_offer", "annual_agreement.accepted_at",
                                     "role", "complimentary_entitlement"), 1))
    async for u in db.users.find({**eligible, "closing": {"$ne": True}}, projection):
        members[u["user_id"]] = (u.get("couples_preferences") or {}).get("analytics_opt_in") is True
        prefs = u.get("couples_preferences") or {}
        since = timestamp(prefs.get("review_return_since"))
        if prefs.get("review_return_opt_in") is True and since:
            review_since[u["user_id"]] = since
        if is_adult(u):
            participating_adults.add(u["user_id"])
            adult_members[u["user_id"]] = u
            # This is an operational access count. Paid conversions and revenue
            # continue to come only from the verified payment transactions below.
            if complimentary_access(u) or quota_period(u, current.timestamp()):
                funded_sponsors.add(u["user_id"])
        annual_authorizations += in_window(timestamp((u.get("annual_agreement") or {}).get("accepted_at")))
        offered += in_window(timestamp(u.get("couples_first_offer_at")))
        started += in_window(timestamp(u.get("couples_first_draft_at")))

    first_invites = {}
    milestones = {"first_mutual": 0, "first_agreement": 0, "both_checked_in": 0}
    connections = dict.fromkeys(("invitations_accepted", "invitations_expired", "invitations_canceled", "pairs_ended"), 0)
    windows = {"pairing": 7, "mutual": 7, "adoption": 7, "completion": 14, "return": 30}
    observed = {key: fraction(0, 0) for key in windows}
    mature = {key: fraction(0, 0) for key in windows}
    one_share = 0

    def count_cohort(key, anchor, achieved):
        if not in_window(anchor):
            return
        deadline = anchor + timedelta(days=windows[key])
        success = bool(achieved and anchor <= achieved <= min(current, deadline))
        observed[key]["eligible"] += 1
        observed[key]["count"] += success
        if deadline <= current:
            mature[key]["eligible"] += 1
            mature[key]["count"] += success
    awaiting_accept = awaiting_confirm = due_pending = 0
    checkin_counts = [0, 0, 0]
    def scheduled_counts():
        results = dict.fromkeys(("eligible", "returned", "both_returned", "both_shared"), 0)
        return {"due": 0, "pending": 0, **results, "observed_so_far": results}

    scheduled = scheduled_counts()
    outcomes = [0, 0, 0, 0]
    outcome_labels = ["Both reported helped", "One reported helped", "Neither reported helped", "At least one did not try it"]
    card_returns = {key: {**scheduled_counts(), "outcome_counts":[0,0,0,0]} for key in CARDS}
    elapsed = []
    active_pairs = 0
    assessment_sharing = {"active_pairs": 0, "any_sharing_pairs": 0,
                          "personality_mutual_pairs": 0, "intimacy_mutual_pairs": 0}
    sponsored_pairs = annual_offers = 0
    shared_features = dict.fromkeys(("prompt", "moment", "positive", "learning"), 0)
    card_funnel = {key: {"id": key, "name": card["name"], "started": 0, "from_card": 0, "from_sample": 0,
                         "mutual": 0, "agreed": 0, "reviewed": 0} for key, card in CARDS.items()}
    tone_usage = {"private": 0, "shared": 0, "either": 0}
    projection = dict.fromkeys(("state", "inviter", "invitee", "created_at", "accepted_at", "joined_at",
                                "expires_at", "closed_at", "close_reason"), 1)
    projection["_id"] = 0
    projection.update(dict.fromkeys(("sponsor", "benefit_members", "annual_opens_at"), 1))
    projection["compatibility_sharing"] = 1
    projection.update({"stats." + key: 1 for key in (
        "first_share_at", "first_mutual_at", "first_agreement_at", "first_review_date", "first_checkins",
        "first_both_checked_in_at", "first_agreement_thread_id", "second_mutual_at", "current_outcomes", "first_due_tracking", "first_due_visits")})
    projection.update({f"stats.cards.{key}.{field}": 1 for key in CARDS for field in
                       ("started_at", "origin", "mutual_at", "agreed_at", "reviewed_at")})
    projection["shared_activities"] = shared_activities_projection()
    projection.update({"stats.tone_usage.private_last_at": 1, "stats.tone_usage.shared_last_at": 1})
    async for pair in db.couples_pairs.aggregate([{"$match": {"env": env}}, {"$project": projection}]):
        owner, other = pair.get("inviter"), pair.get("invitee")
        if owner not in members or (other and other not in members):
            continue
        if owner in participating_adults and other in participating_adults:
            for key, stats in ((pair.get("stats") or {}).get("cards") or {}).items():
                first = timestamp(stats.get("started_at"))
                if key not in card_funnel or not in_window(first):
                    continue
                row = card_funnel[key]
                row["started"] += 1
                if stats.get("origin") in {"card", "sample"}:
                    row["from_" + stats["origin"]] += 1
                for stage in ("mutual", "agreed", "reviewed"):
                    reached = timestamp(stats.get(stage + "_at"))
                    row[stage] += bool(reached and first <= reached <= current)
        annual_offers += in_window(timestamp(pair.get("annual_opens_at")))
        if pair["state"] == "active" and owner in participating_adults and other in participating_adults:
            active_pairs += 1
            try:
                from .couples_compatibility import sharing
            except ImportError:
                from couples_compatibility import sharing
            a, b = sharing(pair, "a", adult_members[owner]), sharing(pair, "b", adult_members[other])
            assessment_sharing["active_pairs"] += 1
            assessment_sharing["any_sharing_pairs"] += any(a[k] or b[k] for k in ("personality", "intimacy"))
            for scope in ("personality", "intimacy"):
                assessment_sharing[scope + "_mutual_pairs"] += bool(a[scope] and b[scope])
            if pair.get("sponsor") in funded_sponsors and set(pair.get("benefit_members") or ()) == {owner, other}:
                sponsored_pairs += 1
            for feature in pair["shared_activities"]:
                shared_features[feature] += 1
            usage = (pair.get("stats") or {}).get("tone_usage") or {}
            used = [mode for mode in ("private", "shared") if in_window(timestamp(usage.get(mode + "_last_at")))]
            for mode in used:
                tone_usage[mode] += 1
            tone_usage["either"] += bool(used)
        created = timestamp(pair.get("created_at"))
        joined = timestamp(pair.get("joined_at"))
        expires = timestamp(pair.get("expires_at"))
        closed = timestamp(pair.get("closed_at"))
        connections["invitations_accepted"] += in_window(timestamp(pair.get("accepted_at")))
        if joined:
            connections["pairs_ended"] += pair["state"] == "closed" and in_window(closed)
        elif expires and expires <= current and (not closed or expires <= closed):
            # A link expires on its deadline even if nobody revisits it to mark it closed.
            connections["invitations_expired"] += in_window(expires)
        elif pair["state"] == "closed" and pair.get("close_reason") == "left":
            connections["invitations_canceled"] += in_window(closed)
        if not created:
            continue
        first_created, first_joined = first_invites.get(owner, (created, None))
        first_invites[owner] = (min(first_created, created), min(first_joined, joined) if first_joined and joined else first_joined or joined)
        # An inviter can replace a still-open link. That extends the expiry,
        # while its original created_at continues to anchor the conversion cohort.
        if expires and expires > current:
            awaiting_accept += pair["state"] == "invited"
            awaiting_confirm += pair["state"] == "confirming"
        if not joined:
            continue
        stats = pair.get("stats") or {}
        first_share = timestamp(stats.get("first_share_at"))
        mutual = timestamp(stats.get("first_mutual_at"))
        agreed = timestamp(stats.get("first_agreement_at"))
        second = timestamp(stats.get("second_mutual_at"))
        by_side = stats.get("first_checkins") or {}
        reviews = [review for side in ("a", "b") if (review := timestamp(by_side.get(side)))]
        both_reviewed = timestamp(first_joint_checkin(stats))
        due = scheduled_review_start(stats.get("first_review_date"))
        milestones["first_mutual"] += in_window(mutual)
        milestones["first_agreement"] += in_window(agreed)
        milestones["both_checked_in"] += in_window(both_reviewed)
        count_cohort("mutual", joined, mutual)
        count_cohort("adoption", mutual, agreed)
        joint_after_agreement = both_reviewed if agreed and both_reviewed and joined <= agreed <= both_reviewed else None
        count_cohort("completion", joined, joint_after_agreement)
        count_cohort("return", both_reviewed, second)
        if in_window(joined) and joined + timedelta(days=7) <= current:
            cutoff = joined + timedelta(days=7)
            if mutual and joined <= mutual <= cutoff:
                elapsed.append((mutual - joined).total_seconds() / 3600)
            elif first_share and joined <= first_share <= cutoff:
                one_share += 1
        if (stats.get("first_due_tracking") and due
                and owner in participating_adults and other in participating_adults and agreed
                and all(who in review_since and review_since[who] <= agreed for who in (owner, other))):
            if in_window(due) and agreed <= current:
                thread = stats.get("first_agreement_thread_id", "")
                card_id = next((key for key in CARDS if thread.startswith("card-"+key+"-")), None)
                group = card_returns.get(card_id)
                targets = [scheduled] if group is None else [scheduled, group]
                end = due + timedelta(days=8)
                review_window_closed = current >= end
                visits = stats.get("first_due_visits") or {}
                first_visits = (timestamp(visits.get(side)) for side in ("a", "b"))
                returned = sum(1 for visit in first_visits if visit and due <= visit < end and visit <= current)
                shared = bool(both_reviewed and agreed <= both_reviewed < end and both_reviewed <= current)
                for target in targets:
                    target["due"] += 1
                    target["pending"] += not review_window_closed
                    windows_to_count = [target["observed_so_far"]]
                    if review_window_closed:
                        windows_to_count.append(target)
                    for counts in windows_to_count:
                        counts["eligible"] += 1
                        counts["returned"] += returned > 0
                        counts["both_returned"] += returned == 2
                        counts["both_shared"] += shared
                if group and review_window_closed and shared and members[owner] and members.get(other) and pair["state"] == "active":
                    choices = stats.get("current_outcomes") or {}
                    values = [choices.get("a"),choices.get("b")]
                    if all(v in {"helped","mixed","didnt_help","not_tried"} for v in values):
                        group["outcome_counts"][3 if "not_tried" in values else 2-values.count("helped")] += 1
        if in_window(agreed) and due:
            # Include the whole scheduled review day, followed by seven full grace days.
            if due <= current - timedelta(days=8):
                deadline = due + timedelta(days=8)
                checkin_counts[sum(agreed <= review < deadline for review in reviews)] += 1
            else:
                due_pending += 1
        if in_window(both_reviewed):
            if (members[owner] and members.get(other) and pair["state"] == "active"
                    and owner in participating_adults and other in participating_adults):
                choices = stats.get("current_outcomes") or {}
                values = [choices.get("a"), choices.get("b")]
                if all(v in {"helped", "mixed", "didnt_help", "not_tried"} for v in values):
                    idx = 3 if "not_tried" in values else 2 - values.count("helped")
                    outcomes[idx] += 1
    for item in card_returns.values():
        item["outcomes"] = disclosure(outcome_labels,item.pop("outcome_counts"))
    issued = [(at, joined) for at, joined in first_invites.values() if in_window(at)]
    for at, joined in issued:
        count_cohort("pairing", at, joined)
    return {
        "days": days, "start": start.date().isoformat(), "end": current.date().isoformat(),
        "offered": offered, "started": started, "issued": len(issued),
        "awaiting_accept": awaiting_accept, "awaiting_confirm": awaiting_confirm, "milestones": milestones,
        "connections": connections,
        "membership": {"sponsored_pairs": sponsored_pairs, "annual_offers": annual_offers,
                       "annual_authorizations": annual_authorizations,
                       **await paid_partner_memberships(db, env, eligible, start, current)},
        "shared_features": {"active_pairs": active_pairs, "counts": shared_features},
        "card_funnel": list(card_funnel.values()), "scheduled_returns": scheduled,
        "card_scheduled_returns": [{"id":key,"name":CARDS[key]["name"],**row} for key,row in card_returns.items()],
        "tone_usage": {"active_pairs": active_pairs, **tone_usage},
        "assessment_sharing": assessment_sharing,
        **mature, "observed_so_far": observed, "confirmed": observed["mutual"]["eligible"], "one_share": one_share,
        "median_hours": round(median(elapsed), 1) if elapsed else None,
        "pending_14d": observed["completion"]["eligible"] - mature["completion"]["eligible"],
        "reviews": {"zero": checkin_counts[0], "one": checkin_counts[1], "both": checkin_counts[2], "pending": due_pending},
        "outcomes": {"minimum": MINIMUM_RESPONDENTS, **disclosure(
            outcome_labels, outcomes)}
    }
