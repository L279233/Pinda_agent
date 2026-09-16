from pathlib import Path

from backend.api.router import api_router
from backend.api.v1.exam import (
    OnlineAnswer,
    OnlineExamResponse,
    OnlineExamSubmitRequest,
    OnlineQuestionResponse,
    _initial_exam_state,
)


def test_online_exam_routes_and_payload_schema_are_registered():
    paths = {route.path for route in api_router.routes}
    assert "/exam/online/{application_id}" in paths
    assert "/exam/online/submit" in paths
    payload = OnlineExamSubmitRequest(
        application_id="00000000-0000-0000-0000-000000000001",
        answers=[OnlineAnswer(question_id="00000000-0000-0000-0000-000000000002", answer="A")],
    )
    assert payload.answers[0].answer == "A"


def test_online_exam_reuses_graph_without_word_parser():
    source = Path(__file__).with_name("nodes.py").read_text(encoding="utf-8")
    assert 'state.get("submission_source") == "online"' in source
    assert 'return {"parsed_questions": state.get("parsed_questions", [])}' in source


def test_online_exam_state_contains_recruitment_context():
    class Context:
        application_id = "application-1"
        position_id = "position-1"
        position_title = "AI 工程师"
        exam_id = "exam-1"

    state = _initial_exam_state(
        context=Context(), submission_id="submission-1", candidate_id="candidate-1",
        tenant_id="tenant-1", source="online", parsed_questions=[],
    )
    assert state["submission_source"] == "online"
    assert state["application_id"] == "application-1"


def test_online_question_response_does_not_expose_answers_or_scoring_points():
    response = OnlineExamResponse(
        application_id="application-1",
        position_title="AI Engineer",
        questions=[
            OnlineQuestionResponse(
                question_id="question-1",
                question_no=1,
                question_type="objective",
                content="Question",
                score=10,
            )
        ],
    ).model_dump()

    question_fields = set(response["questions"][0])
    assert question_fields == {
        "question_id", "question_no", "question_type", "content", "score"
    }
    assert {"correct_answer", "reference_answer", "scoring_points"}.isdisjoint(
        question_fields
    )
