from backend.api.router import api_router
from backend.api.v1.recruiter import (
    KnowledgeDocumentItem,
    RecruiterApplicationItem,
    RecruiterPositionItem,
)
from pathlib import Path


def test_recruiter_routes_are_registered():
    paths = {route.path for route in api_router.routes}
    assert "/recruiter/applications" in paths
    assert "/recruiter/applications/{application_id}" in paths
    assert "/recruiter/applications/{application_id}/decision" in paths
    assert "/recruiter/positions" in paths
    assert "/recruiter/positions/{position_id}" in paths
    assert "/recruiter/exams" in paths
    assert "/recruiter/knowledge-documents" in paths


def test_recruiter_summary_has_scores_but_no_candidate_task_shape():
    fields = RecruiterApplicationItem.model_fields
    assert "resume_score" in fields
    assert "exam_score" in fields
    assert "interview_score" in fields
    assert "report" not in fields


def test_final_decision_is_persisted_with_audit_history():
    source = Path(__file__).with_name("recruiter.py").read_text(encoding="utf-8")
    assert "INSERT INTO recruitment_decision_audits" in source
    assert "WHEN :decision_check = 'withdrawn' THEN 'withdrawn'" in source
    assert '"decision_history"' in source


def test_recruiter_management_models_expose_operational_state():
    assert {"exam_id", "status", "application_count"}.issubset(
        RecruiterPositionItem.model_fields
    )
    assert {"document_type", "status", "chunk_count", "error_msg"}.issubset(
        KnowledgeDocumentItem.model_fields
    )
