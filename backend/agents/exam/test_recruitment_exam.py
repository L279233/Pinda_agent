import unittest

from backend.agents.exam.nodes import _needs_manual_review, apply_teacher_decision_node
from backend.agents.exam.prompts import SYSTEM_PROMPT
from backend.agents.exam.state import ExamState
from backend.api.v1.exam import _candidate_submission_payload


class RecruitmentExamRulesTest(unittest.TestCase):
    def test_graph_state_carries_application_context(self):
        required = {"application_id", "position_id", "position_title", "exam_id"}
        self.assertTrue(required.issubset(ExamState.__annotations__))

    def test_only_low_confidence_results_require_manual_review(self):
        self.assertFalse(_needs_manual_review({"pre_review_summary": {"needs_review_count": 0}}))
        self.assertTrue(_needs_manual_review({"pre_review_summary": {"needs_review_count": 1}}))

    def test_candidate_payload_contains_no_evaluation_results(self):
        forbidden = {
            "score", "final_score", "full_score", "score_rate", "by_question",
            "weak_points", "report", "ranking", "needs_review", "teacher_comment",
        }
        for db_status in ("submitted", "ai_processing", "pending_review", "published"):
            payload = _candidate_submission_payload("submission-1", db_status)
            self.assertTrue(forbidden.isdisjoint(payload))
            self.assertNotIn("review", str(payload).lower())

    def test_recruitment_prompt_uses_candidate_language(self):
        self.assertIn("招聘笔试", SYSTEM_PROMPT)
        self.assertNotIn("课程助教", SYSTEM_PROMPT)


class RecruitmentExamAsyncRulesTest(unittest.IsolatedAsyncioTestCase):
    async def test_recruiter_score_is_clamped_to_question_range(self):
        state = {
            "teacher_decision": {
                "action": "modify",
                "teacher_id": "recruiter-1",
                "modifications": [{"question_id": "q1", "new_score": 99}],
            },
            "pre_review_summary": {
                "by_question": [{"question_id": "q1", "score": 3, "full_score": 10}]
            },
        }
        result = await apply_teacher_decision_node(state)
        self.assertEqual(result["final_results"][0]["final_score"], 10)


if __name__ == "__main__":
    unittest.main()
