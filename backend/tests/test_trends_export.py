"""Pure export regressions: no database, identities, or provider calls."""
import csv
from io import StringIO

import pytest

from backend.trends_export import export_report


def exported_rows(cents):
    suppressed = {"available": False}
    workspace = {"start": "2026-11-07", "end": "2026-11-13",
        "totals": {"reads": 7}, "measures": [{"key": "reads", "unit": "Saved reads"}],
        "daily": [{"day": "2026-11-13", "reads": 7}], "subscriptions": {}, "saved_features": [],
        "outcomes": suppressed, "everyday": {"questions": []}}
    couples = {key: 0 for key in ("offered", "started", "issued", "confirmed", "pending_14d", "one_share",
        "awaiting_accept", "awaiting_confirm")}
    couples.update(median_hours=None, membership={}, milestones={}, connections={}, reviews={}, outcomes=suppressed,
        shared_features={"active_pairs": 0, "counts": {}}, **{key: {"count": 0, "eligible": 0}
        for key in ("pairing", "mutual", "adoption", "completion", "return")})
    dashboard = {key: 0 for key in ("users", "onboarding_skipped", "sessions", "returning_users", "returning_days",
        "memory_edits", "memory_edits_by_current_holders", "users_with_memories", "memory_edit_rate", "waitlist")}
    dashboard.update(quiz={"sections": {}, "users": 0}, activation=[], onboarding_steps={},
        profile_progress={"started": 0, "with_details": 0, "members": 0},
        deepening_rate={"deepened_within_7d": 0, "first_session_users": 0},
        deepening_window={"deepened": 0, "complete": 0, "open": 0}, by_origin=[], upload_completion={},
        **{key: {} for key in ("people_by_relationship", "people_by_group", "avg_detail_by_relationship", "avg_detail_by_tier")})
    membership = {"total_users": 2, "monthly_revenue_cents": cents, "month_start": "2026-11-01"}
    return list(csv.DictReader(StringIO(export_report(workspace, couples, dashboard, {"counts": {}}, membership))))


@pytest.mark.parametrize(("cents", "value", "status"), [
    (None, "", "pending reconciliation"), (0, "0.00", "available"),
    (1200, "12.00", "available"), (-101, "-1.01", "available"), (-1, "-0.01", "available"),
])
def test_workspace_csv_preserves_unknown_and_signed_net_revenue(cents, value, status):
    rows = exported_rows(cents)
    revenue = next(row for row in rows if row["metric"] == "subscription_revenue_usd")
    assert revenue["value"] == value
    assert revenue["status"] == status
    assert revenue["scope"] == "calendar month to date"
    assert revenue["from_utc"] == "2026-11-01"
    assert "verified refund or lost-dispute adjustments" in revenue["definition"]
    assert next(row for row in rows if row["section"] == "Activity" and row["scope"] == "period")["value"] == "7"
