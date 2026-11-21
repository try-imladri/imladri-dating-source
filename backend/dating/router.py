"""Purpose-limited dating API. Inject account and staff authentication from host."""
import base64

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from .. import accounts
from . import schemas as s, service as d


def make_router(get_db, member_dependency, admin_dependency):
    async def private_response(response: Response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"

    router = APIRouter(prefix="/dating", tags=["dating"], dependencies=[Depends(private_response)])

    @router.get("/bootstrap")
    async def bootstrap(member=Depends(member_dependency)):
        return await d.bootstrap(get_db(), member)

    @router.post("/enrollment")
    async def enroll(body: s.Enrollment, member=Depends(member_dependency)):
        return await d.enroll(get_db(), member, body)

    @router.delete("/enrollment")
    async def leave(request: Request, body: s.Confirm, member=Depends(member_dependency)):
        async with accounts.exclusive_content_clear(get_db(), request, member["user_id"]):
            await d.clear_member(get_db(), member["user_id"], d.env_of(member))
        return {"ok": True, "notice": "Your dating participation was removed. Your other Imladri spaces remain available."}

    @router.get("/profile")
    async def profile(member=Depends(member_dependency)):
        return {"profile": await d.present_owner(get_db(), await d.own_profile(get_db(), member))}

    @router.put("/profile")
    @router.patch("/profile")
    async def save_profile(body: s.Profile, member=Depends(member_dependency)):
        return await d.save_profile(get_db(), member, body)

    @router.post("/profile/submit")
    async def submit(member=Depends(member_dependency)):
        return await d.submit_profile(get_db(), member)

    @router.post("/profile/state")
    async def state(body: s.ProfileState, member=Depends(member_dependency)):
        return await d.profile_state(get_db(), member, body.state)

    @router.get("/photos")
    async def photos(member=Depends(member_dependency)):
        return {"items": await d.photos_for(get_db(), await d.own_profile(get_db(), member), owner=True)}

    @router.post("/photos")
    async def photo(file: UploadFile = File(...), member=Depends(member_dependency)):
        await d.rate(get_db(), member, "photo", 30)
        try:
            data = await file.read(d.MAX_PHOTO_BYTES + 1)
        finally:
            await file.close()
        return await d.add_photo(get_db(), member, data)

    @router.get("/photos/{photo_id}/content")
    async def content(photo_id: str, member=Depends(member_dependency)):
        db, env = get_db(), d.env_of(member)
        row = await db.dating_photos.find_one({"env": env, "id": photo_id})
        if not row:
            raise HTTPException(404, "Photo unavailable.")
        profile = await db.dating_profiles.find_one({"env": env, "user_id": row["user_id"], "photo_ids": photo_id})
        if not profile:
            raise HTTPException(404, "Photo unavailable.")
        if row["user_id"] != member["user_id"] and member.get("role") not in ("admin", "staff", "super_admin"):
            await d.enrollment(db, member)
            await d.accessible_profile(db, member, profile["id"])
            if row["state"] != "approved" or photo_id not in profile.get("published", {}).get("photo_ids", []):
                raise HTTPException(404, "Photo unavailable.")
        return Response(base64.b64decode(row["data"]), media_type=row["mime"], headers={"Cache-Control": "private, no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"})

    @router.delete("/photos/{photo_id}")
    async def remove_photo(photo_id: str, member=Depends(member_dependency)):
        return await d.delete_photo(get_db(), member, photo_id)

    @router.get("/preferences")
    async def preferences(member=Depends(member_dependency)):
        return {"preferences": await d.prefs(get_db(), member["user_id"], d.env_of(member))}

    @router.put("/preferences")
    async def save_preferences(body: s.Preferences, member=Depends(member_dependency)):
        return await d.save_preferences(get_db(), member, body)

    @router.get("/discovery")
    async def discovery(limit: int = Query(24, ge=1, le=50), member=Depends(member_dependency)):
        return await d.discovery(get_db(), member, limit)

    @router.get("/profiles/{profile_id}")
    async def public_profile(profile_id: str, member=Depends(member_dependency)):
        await d.enrollment(get_db(), member)
        row = await d.accessible_profile(get_db(), member, profile_id)
        return await d.safe_profile(get_db(), row, await d.own_profile(get_db(), member))

    @router.post("/interests")
    async def interest(body: s.Interest, member=Depends(member_dependency)):
        await d.rate(get_db(), member, "interest", 100)
        return await d.interest(get_db(), member, body)

    @router.get("/likes")
    async def likes(member=Depends(member_dependency)):
        return await d.incoming_likes(get_db(), member)

    @router.get("/matches")
    async def matches(member=Depends(member_dependency)):
        return await d.matches(get_db(), member)

    @router.get("/matches/{match_id}/messages")
    async def messages(match_id: str, cursor: str | None = Query(None, max_length=80), limit: int = Query(50, ge=1, le=100), member=Depends(member_dependency)):
        return await d.message_page(get_db(), member, match_id, cursor, limit)

    @router.post("/matches/{match_id}/messages")
    async def send(match_id: str, body: s.Message, member=Depends(member_dependency)):
        await d.rate(get_db(), member, "message", 120, 60)
        return await d.send_message(get_db(), member, match_id, body)

    @router.post("/matches/{match_id}/read")
    async def read(match_id: str, member=Depends(member_dependency)):
        return await d.mark_read(get_db(), member, match_id)

    @router.post("/matches/{match_id}/unmatch")
    async def unmatch(match_id: str, member=Depends(member_dependency)):
        return await d.unmatch(get_db(), member, match_id)

    @router.get("/blocks")
    async def blocks(member=Depends(member_dependency)):
        return await d.block_list(get_db(), member)

    @router.post("/blocks")
    async def block(body: s.Block, member=Depends(member_dependency)):
        return await d.block_member(get_db(), member, body.profile_id)

    @router.delete("/blocks/{profile_id}")
    async def unblock(profile_id: str, member=Depends(member_dependency)):
        return await d.unblock(get_db(), member, profile_id)

    @router.post("/reports")
    async def report(body: s.Report, member=Depends(member_dependency)):
        await d.rate(get_db(), member, "report", 20)
        return await d.report_member(get_db(), member, body)

    @router.get("/matches/{match_id}/sharing")
    async def shared(match_id: str, member=Depends(member_dependency)):
        return await d.shared_answers(get_db(), member, match_id)

    @router.post("/matches/{match_id}/sharing")
    async def share(match_id: str, body: s.Sharing, member=Depends(member_dependency)):
        return await d.share_answers(get_db(), member, match_id, body)

    @router.delete("/matches/{match_id}/sharing")
    async def revoke(match_id: str, member=Depends(member_dependency)):
        return await d.revoke_share(get_db(), member, match_id)

    @router.post("/matches/{match_id}/feedback")
    async def feedback(match_id: str, body: s.Feedback, member=Depends(member_dependency)):
        return await d.feedback(get_db(), member, match_id, body)

    @router.get("/export")
    async def export(member=Depends(member_dependency)):
        return await d.own_export(get_db(), member["user_id"], d.env_of(member))

    @router.post("/appeals")
    async def appeal(body: s.Appeal, member=Depends(member_dependency)):
        return await d.appeal(get_db(), member, body)

    @router.get("/admin/queue")
    async def queue(member=Depends(admin_dependency)):
        return await d.admin_queue(get_db(), member)

    @router.get("/admin/metrics")
    async def metrics(member=Depends(admin_dependency)):
        return await d.metrics(get_db(), member)

    @router.put("/admin/settings")
    async def settings(body: s.LaunchSettings, member=Depends(admin_dependency)):
        return await d.save_settings(get_db(), member, body)

    @router.post("/admin/profiles/{profile_id}/review")
    async def review_profile(profile_id: str, body: s.Review, member=Depends(admin_dependency)):
        return await d.review_profile(get_db(), member, profile_id, body)

    @router.post("/admin/photos/{photo_id}/review")
    async def review_photo(photo_id: str, body: s.Review, member=Depends(admin_dependency)):
        return await d.review_photo(get_db(), member, photo_id, body)

    @router.post("/admin/reports/{report_id}/review")
    async def review_report(report_id: str, body: s.ReportReview, member=Depends(admin_dependency)):
        return await d.review_case(get_db(), member, "dating_reports", report_id, body)

    @router.post("/admin/appeals/{appeal_id}/review")
    async def review_appeal(appeal_id: str, body: s.ReportReview, member=Depends(admin_dependency)):
        return await d.review_case(get_db(), member, "dating_appeals", appeal_id, body)

    return router
