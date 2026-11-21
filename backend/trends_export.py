"""Spreadsheet-ready aggregate rows for the Admin trends download."""
import csv
from decimal import Decimal
import re
from io import StringIO


DEFINITIONS = {
    "started a people profile": "Members with at least one saved people profile / eligible members, starting at signup.",
    "saved profile details": "Members with any nonblank saved intake field / eligible members, including before their first read.",
    "first-week profile updates observed so far": "Members with a substantial profile edit within seven days of first read / all first readers as of this report, including today.",
    "profile deepened within 7 days": "Members with a substantial profile edit during the first week / readers who have had seven full days.",
    "first-read windows still open": "Readers with less than seven days since their first read. Included in the observed-so-far count.",
    "total_accounts": "All accounts in this environment, including staff. Matches Users & Membership.",
    "subscription_revenue_usd": "Paid USD subscriptions this UTC calendar month after discounts and verified refund or lost-dispute adjustments, before fees. Sales tax is excluded when invoice details are available. Unverified net revenue is blank pending reconciliation.",
    "active": "Members with a verified current paid period at export time.",
    "replies_used": "Settled replies within active subscribers' current billing periods.",
    "pairing": "First-inviting sponsors whose pair confirmed within 7 days / sponsors observed for 7 days.",
    "mutual": "Confirmed pairs who both shared within 7 days / pairs observed for 7 days.",
    "adoption": "Pairs agreeing on a revision within 7 days of first mutual share / pairs observed for 7 days after sharing.",
    "completion": "Confirmed pairs whose first agreement and joint check-in reveal occurred within 14 days / pairs observed for 14 days. Older records use the two first-share timestamps.",
    "return": "Pairs starting another mutual conversation in 30 days / pairs observed for 30 days after both checked in.",
    "median_hours": "Median hours from pair confirmation to mutual share among qualifying 7-day completers.",
    "shared_checkins": "First agreements by number of partners who shared by the review date plus 7 days; pending are still inside that window.",
    "first_mutual": "Pairs reaching their first mutually shared conversation within the selected period.",
    "first_agreement": "Pairs reaching their first mutually accepted agreement within the selected period.",
    "both_checked_in": "Pairs reaching both shared check-ins on their first agreement within the selected period.",
    "invitations_accepted": "Invitations accepted by the recipient during the selected period, before inviter confirmation.",
    "invitations_expired": "Unconfirmed invitations whose expiry fell in the selected period, including links still awaiting cleanup.",
    "invitations_canceled": "Unconfirmed invitations canceled before expiry during the selected period.",
    "pairs_ended": "Previously confirmed pairs that ended their connection during the selected period.",
    "card_started": "Distinct eligible pairs starting this card for the first time during the period.",
    "card_from_card": "Pairs whose first start came from the card list / pairs starting this card in the period.",
    "card_from_sample": "Pairs whose first start came from a sample / pairs starting this card in the period.",
    "card_mutual": "Pairs where both shared selected answer lines / pairs starting this card in the period.",
    "card_agreed": "Pairs where both accepted the same agreement revision / pairs starting this card in the period.",
    "card_reviewed": "Pairs where both shared check-ins on the same agreement / pairs starting this card in the period.",
    "shared_features": "Active pairs currently sharing this feature / active pairs. Each pair counts once per feature. Learnings require approval by both partners.",
    "sponsored_pairs": "Currently connected adult pairs with a redeemed sponsored seat and a sponsor in a verified paid period.",
    "partner_upgrades": "Member accounts with a paid initial invited-partner purchase during the selected period.",
    "annual_offers": "Couples whose first qualifying partner payment opened a 24-hour annual offer during the selected period.",
    "annual_authorizations": "Accounts that authorized their own annual offer during the period, including later cancellations.",
    "annual_payments": "Accounts with a verified paid annual partner invoice during the period.",
}


def spreadsheet_cell(value):
    if isinstance(value, str):
        if value.startswith(("\t", "\r", "\n")) or re.match(r"^[\s\ufeff]*[=+@-]", value):
            return "'" + value
    return value


def export_report(workspace, couples, dashboard, prompts, membership):
    target = StringIO(newline="")
    writer = csv.writer(target)
    writer.writerow(("section", "scope", "from_utc", "through_utc", "metric", "category",
                     "value", "denominator", "status", "definition"))
    start, end = workspace["start"], workspace["end"]
    activity_definitions = {measure["key"]: measure["unit"] for measure in workspace["measures"]}

    def row(section, scope, metric, value=None, denominator=None, category="", status="available", day=None, since=None, definition=None):
        first = day or since or (end if scope == "current snapshot" else start)
        definitions = activity_definitions if section == "Activity" else DEFINITIONS
        cells = (section, scope, first, day or end, metric, category, value, denominator, status,
                 definition if definition is not None else definitions.get(metric, ""))
        writer.writerow(spreadsheet_cell(cell) for cell in cells)

    def breakdown(section, scope, metric, group):
        if not group["available"]:
            row(section, scope, metric, status="suppressed")
            return
        for option in group["options"]:
            row(section, scope, metric, option["count"], group["respondents"], option["label"])

    for metric, count in workspace["totals"].items():
        row("Activity", "period", metric, count)
    if tone := workspace.get("tone_usage"):
        denominators = {"estimated": "reads", "insufficient": "reads", "selected": "reads", "switched": "selected",
                        "copied": "reads", "reviewed": "reads", "corrected": "reviewed",
                        "reported_sending": "reads", "sent": "reported_sending", "rated_helpfulness": "sent"}
        for metric in ("members", "reads", *denominators):
            row("Individual tone", "read creation period", metric, tone[metric], tone.get(denominators.get(metric), ""))
        for style, count in tone["styles"].items():
            row("Individual tone", "read creation period", "style tried", count, tone["selected"], style)
    def scheduled_rows(section, data, category=""):
        labels = {
            "eligible": "Pairs whose first scheduled review is due.",
            "returned": "Due pairs where at least one partner viewed the check-in.",
            "both_returned": "Due pairs where each partner viewed the check-in.",
            "both_shared": "Due pairs where both first-agreement check-ins were shared by the deadline.",
        }
        for metric, description in labels.items():
            if observed := data.get("observed_so_far"):
                row(section, "observed so far", metric, observed[metric],
                    observed["eligible"] if metric != "eligible" else None, category,
                    definition=description + " Includes open windows through the report instant.")
        for metric in ("due", "pending", *labels):
            row(section, "first review due in period, UTC", metric, data[metric],
                data["eligible"] if metric in ("returned", "both_returned", "both_shared") else None, category,
                definition=(labels[metric] + " Counts fully elapsed review windows only.") if metric in labels else None)

    if scheduled := couples.get("scheduled_returns"):
        scheduled_rows("Couples scheduled check-ins", scheduled)
    for card in couples.get("card_funnel", []):
        for stage in ("started", "from_card", "from_sample", "mutual", "agreed", "reviewed"):
            row("Couples cards", "first card start in period", "card_" + stage, card[stage],
                None if stage == "started" else card["started"], card["name"])
    for card in couples.get("card_scheduled_returns", []):
        scheduled_rows("Couples card scheduled check-ins", card, card["name"])
        if card["outcomes"]["available"]:
            for option in card["outcomes"]["options"]:
                row("Couples card shared results", "mature first review with two shared choices", option["label"],
                    option["count"],card["outcomes"]["respondents"],card["name"])
    if tone := couples.get("tone_usage"):
        for mode in ("private", "shared", "either"):
            row("Couples tone", "active pairs using coaching in period", mode, tone[mode], tone["active_pairs"])
    for record in workspace["daily"]:
        for metric in workspace["totals"]:
            row("Activity", "UTC day", metric, record[metric], day=record["day"])
    for metric, value in workspace["subscriptions"].items():
        row("Subscriptions", "current snapshot", metric, value)
    for feature in workspace["saved_features"]:
        row("Individual saved features", "current snapshot", feature["key"], feature["count"], category=feature["label"])
    for metric in ("offered", "started", "issued", "confirmed", "pending_14d",
                   "one_share", "median_hours"):
        value = couples[metric]
        row("Couples", "period", metric, value, status="available" if value is not None else "no eligible pairs")
    for metric in ("awaiting_accept", "awaiting_confirm"):
        row("Couples", "current snapshot", metric, couples[metric])
    features = couples["shared_features"]
    for metric, count in couples["membership"].items():
        row("Couples membership", "current snapshot" if metric == "sponsored_pairs" else "period", metric, count)
    for category, count in features["counts"].items():
        row("Couples", "current snapshot", "shared_features", count, features["active_pairs"], category)
    for metric, count in couples["milestones"].items():
        row("Couples", "period", metric, count)
    for metric, count in couples["connections"].items():
        row("Couples", "period", metric, count)
    for metric, definition in {
        "pairing": "Sponsors whose first invitation led to a confirmed pair within 7 days / all first-inviting sponsors.",
        "mutual": "Pairs with a mutual share within 7 days of confirmation / all newly confirmed pairs.",
        "adoption": "Pairs agreeing within 7 days of their first mutual share / all pairs reaching their first mutual share.",
        "completion": "Pairs reaching an agreement and both shared check-ins within 14 days of confirmation / all newly confirmed pairs.",
        "return": "Pairs starting another mutual conversation within 30 days of their first joint check-in / all pairs reaching a joint check-in.",
    }.items():
        if metric in couples.get("observed_so_far", {}):
            progress = couples["observed_so_far"][metric]
            row("Couples", "observed so far", metric, progress["count"], progress["eligible"],
                definition=definition + " Includes windows still open at report time.")
        cohort = couples[metric]
        row("Couples", "mature cohort", metric, cohort["count"], cohort["eligible"])
    for category, count in couples["reviews"].items():
        row("Couples", "agreement cohort", "shared_checkins", count, category=category)
    breakdown("Couples", "period", "joint outcome", couples["outcomes"])
    breakdown("Individual", "period", "member outcome", workspace["outcomes"])
    for question in workspace["everyday"]["questions"]:
        breakdown("Everyday preferences", "current snapshot", question["prompt"], question)
    for section, stats in dashboard["quiz"]["sections"].items():
        for stage in ("started", "halfway", "complete"):
            row("Individual quiz", "current snapshot", section, stats[stage], dashboard["quiz"]["users"], stage)
    for stage in dashboard["activation"]:
        row("Account milestones", "current snapshot", stage["name"], stage["value"], dashboard["users"])
    for step, count in dashboard["onboarding_steps"].items():
        row("Individual onboarding", "current snapshot", "reached step", count, dashboard["users"], step)
    row("Individual onboarding", "current snapshot", "skipped guide", dashboard["onboarding_skipped"], dashboard["users"])
    progress = dashboard["profile_progress"]
    row("Individual onboarding", "current snapshot", "started a people profile", progress["started"], progress["members"])
    row("Individual onboarding", "current snapshot", "saved profile details", progress["with_details"], progress["members"])
    observed = dashboard["deepening_rate"]
    row("Individual onboarding", "current snapshot", "first-week profile updates observed so far",
        observed["deepened_within_7d"], observed["first_session_users"])
    deepening = dashboard["deepening_window"]
    row("Individual onboarding", "current snapshot", "profile deepened within 7 days",
        deepening["deepened"], deepening["complete"])
    row("Individual onboarding", "current snapshot", "first-read windows still open", deepening["open"])
    for entry in dashboard["by_origin"]:
        for metric in ("signups", "first_read", "activated", "purchased", "renewed"):
            row("Entry pages", "current snapshot", metric, entry[metric], entry["signups"], entry["origin"])
    for metric in ("people_by_relationship", "people_by_group", "avg_detail_by_relationship", "avg_detail_by_tier"):
        for category, count in sorted(dashboard[metric].items()):
            row("People profiles", "current snapshot", metric, count, category=category)
    for metric in ("sessions", "returning_users", "returning_days", "memory_edits", "memory_edits_by_current_holders", "users_with_memories", "memory_edit_rate", "waitlist"):
        row("Workspace overview", "current snapshot", metric, dashboard[metric])
    for metric, value in dashboard["upload_completion"].items():
        row("Workspace overview", "current snapshot", "uploads_" + metric, value)
    for status in ("succeeded", "failed"):
        row("AI operations", "period", "all features", sum(v[status] for v in prompts["counts"].values()), category=status)
        for feature, calls in sorted(prompts["counts"].items()):
            row("AI operations", "period", feature, calls[status], category=status)
    row("Membership", "current snapshot", "total_accounts", membership["total_users"])
    cents = membership["monthly_revenue_cents"]
    # Preserve missing reconciliation evidence, signed cents and numeric CSV semantics.
    amount = Decimal(cents).scaleb(-2) if cents is not None else None
    row("Membership", "calendar month to date", "subscription_revenue_usd", amount,
        status="available" if cents is not None else "pending reconciliation", since=membership["month_start"])
    return target.getvalue()
