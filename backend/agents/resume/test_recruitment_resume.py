import unittest

from backend.agents.resume.nodes import SIX_DIMENSIONS, _determine_resume_decision
from backend.agents.resume.prompts import DIMENSION_REVIEW_PROMPTS, SYSTEM_PROMPT
from backend.agents.resume.state import ResumeState
from backend.api.v1.resume import _candidate_review_payload
from backend.core.application_service import ResumeDecision


def _decision_state(**overrides):
    state = {
        "raw_text": "具备 FastAPI、LangGraph 和 RAG 项目经验。" * 20,
        "structured": {"name": "候选人"},
        "summary": {"manual_review_required": False},
        "fallback_used": False,
        "weighted_score": 80,
        "resume_pass_score": 70,
    }
    state.update(overrides)
    return state


class RecruitmentResumeRulesTest(unittest.TestCase):
    def test_six_recruitment_dimensions_have_complete_weight(self):
        self.assertEqual(len(SIX_DIMENSIONS), 6)
        self.assertAlmostEqual(sum(item["weight"] for item in SIX_DIMENSIONS), 1.0)
        self.assertEqual({item["key"] for item in SIX_DIMENSIONS}, set(DIMENSION_REVIEW_PROMPTS))

    def test_prompts_require_job_context_and_avoid_protected_attributes(self):
        for prompt in DIMENSION_REVIEW_PROMPTS.values():
            self.assertIn("{job_description}", prompt)
            self.assertIn("{job_requirements}", prompt)
        self.assertIn("受保护", SYSTEM_PROMPT)

    def test_graph_state_carries_application_and_job_context(self):
        required = {
            "application_id", "position_id", "position_title", "job_description",
            "job_requirements", "resume_pass_score",
        }
        self.assertTrue(required.issubset(ResumeState.__annotations__))

    def test_threshold_decision_is_made_in_code(self):
        self.assertEqual(_determine_resume_decision(_decision_state()), ResumeDecision.PASSED)
        self.assertEqual(
            _determine_resume_decision(_decision_state(weighted_score=69.99)),
            ResumeDecision.REJECTED,
        )

    def test_fallback_or_low_confidence_requires_manual_review(self):
        self.assertEqual(
            _determine_resume_decision(_decision_state(fallback_used=True)),
            ResumeDecision.MANUAL_REVIEW,
        )
        self.assertEqual(
            _determine_resume_decision(
                _decision_state(summary={"manual_review_required": True})
            ),
            ResumeDecision.MANUAL_REVIEW,
        )

    def test_candidate_payload_never_contains_evaluation_results(self):
        forbidden = {
            "weighted_score", "scores", "dimension_scores", "issues", "summary",
            "decision", "ranking", "report", "error_msg",
        }
        for status in ("processing", "done", "failed"):
            payload = _candidate_review_payload("review-1", status, "internal stack trace")
            self.assertTrue(forbidden.isdisjoint(payload))
            self.assertNotIn("internal stack trace", str(payload))


if __name__ == "__main__":
    unittest.main()
