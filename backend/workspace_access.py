"""Authoritative workspace routing without changing billing or private data ownership."""
from fastapi import HTTPException

try:
    from .free_plan import has_personal_access, subscription_required
except ImportError:
    from free_plan import has_personal_access, subscription_required

VERSION = "workspace-access-v1"


async def paired_record(db, member):
    if member.get("closing") or not member.get("env") or not member.get("user_id"):
        return None
    row = await db.couples_pairs.find_one(
        {"env": member["env"], "state": "active", "members": member["user_id"]},
        {"_id": 1, "members": 1, "inviter": 1, "invitee": 1, "sponsor": 1})
    if not row:
        return None
    members = row.get("members")
    if (not isinstance(members, list) or len(members) != 2 or not all(isinstance(uid, str) and uid for uid in members)
            or len(set(members)) != 2
            or set(members) != {row.get("inviter"), row.get("invitee")}
            or member["user_id"] not in members or row.get("sponsor") not in members):
        raise HTTPException(409, "Your pairing needs review. Reopen Partner & settings.")
    return row


async def snapshot(db, member):
    pair = await paired_record(db, member)
    assessment_access = has_personal_access(member)
    if pair and not assessment_access:
        try:
            await assessment_member(db, member)
            assessment_access = True
        except HTTPException as exc:
            if exc.status_code not in (402, 403, 404):
                raise
    return {"version": VERSION, "mode": "couples" if pair else "individual",
            "couples_only": bool(pair), "pair_id": pair["_id"] if pair else None,
            "individual": not bool(pair), "your_type": not bool(pair),
            "assessments": bool(assessment_access)}


async def require_individual(db, member):
    if await paired_record(db, member):
        raise HTTPException(403, {"code": "couples_workspace_required", "redirect": "/app/couples",
            "message": "Your confirmed pairing uses the Couples workspace. Your assessments are available there."},
            headers={"X-Imladri-Workspace": "couples"})
    return member


async def assessment_member(db, member):
    if has_personal_access(member):
        return member
    if await paired_record(db, member) is None:
        raise subscription_required()
    # A sponsored partner can complete their OWN assessments inside the live
    # Couples space. This grants neither Individual access nor their partner's data.
    try:
        from .couples import active_pair
    except ImportError:
        from couples import active_pair
    try:
        await active_pair(db, member, write=True)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise subscription_required() from None
        raise
    return member


async def assessment_payer(db, member):
    if has_personal_access(member):
        return member
    await assessment_member(db, member)
    pair = await paired_record(db, member)
    if pair is None:
        raise subscription_required()
    sponsor = await db.users.find_one({"user_id": pair["sponsor"],
        "env": {"$in": [member["env"], None]}, "closing": {"$ne": True}})
    if not sponsor or not has_personal_access(sponsor):
        raise subscription_required()
    return sponsor
