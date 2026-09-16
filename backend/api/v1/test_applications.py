from backend.api.router import api_router
from backend.api.v1.applications import PositionItem
from backend.core.application_service import CandidateTaskView


def test_candidate_application_routes_are_registered():
    paths = {route.path for route in api_router.routes}
    assert "/positions" in paths
    assert "/applications" in paths
    assert "/applications/{application_id}/tasks" in paths


def test_position_response_does_not_expose_internal_configuration():
    payload = PositionItem(
        position_id="position-1",
        code="LLM-001",
        title="大模型应用开发工程师",
        jd_text="负责大模型应用开发",
        requirements={"skills": ["Python"]},
    ).model_dump()
    assert {
        "resume_pass_score", "evaluation_weights", "exam_id", "created_by",
    }.isdisjoint(payload)


def test_candidate_task_view_has_no_results_or_decisions():
    fields = CandidateTaskView.model_fields
    assert {
        "score", "report", "ranking", "resume_decision", "final_decision",
    }.isdisjoint(fields)
