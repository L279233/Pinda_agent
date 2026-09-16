from backend.api.router import api_router
from backend.api.v1.notifications import NotificationItem


def test_notification_routes_are_registered():
    paths = {route.path for route in api_router.routes}
    assert "/notifications" in paths
    assert "/notifications/{notification_id}/read" in paths


def test_candidate_notification_contains_no_recruitment_result():
    fields = NotificationItem.model_fields
    assert {"score", "report", "ranking", "decision", "review_status"}.isdisjoint(fields)
