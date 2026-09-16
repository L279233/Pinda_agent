import unittest

from backend.agents.interview.prompts import (
    GENERATE_REPORT_PROMPT,
    INTRO_EVAL_TECH_FIRST_PROMPT,
    SYSTEM_PROMPT,
    TECH_BASE_PROMPT,
    TECH_BASE_GENERATE_PROMPT,
)
from backend.agents.interview.state import InterviewReport, InterviewState
from backend.api.v1.interview import _candidate_finished_payload


class RecruitmentInterviewRulesTest(unittest.TestCase):
    def test_graph_state_carries_application_and_job_context(self):
        required = {
            "application_id", "position_id", "target_position",
            "job_description", "job_requirements", "resume_review_id",
        }
        self.assertTrue(required.issubset(InterviewState.__annotations__))

    def test_prompts_use_job_context_and_protect_candidate_attributes(self):
        self.assertIn("{job_description}", TECH_BASE_GENERATE_PROMPT)
        self.assertIn("{job_requirements}", GENERATE_REPORT_PROMPT)
        self.assertIn("受保护", SYSTEM_PROMPT)

    def test_candidate_prompts_do_not_expose_internal_evaluation(self):
        self.assertIn("不评价", INTRO_EVAL_TECH_FIRST_PROMPT)
        self.assertIn("不得在回复中提及", TECH_BASE_PROMPT["ask_with_feedback"])
        self.assertIn("不得在回复中提及", TECH_BASE_PROMPT["followup"])

    def test_candidate_completion_payload_has_no_report_fields(self):
        forbidden = {
            "overall_score", "dimensions", "strengths", "improvements",
            "report", "evidence", "risk_flags", "decision", "ranking",
        }
        for finished in (False, True):
            payload = _candidate_finished_payload("session-1", finished)
            self.assertTrue(forbidden.isdisjoint(payload))

    def test_internal_report_supports_evidence_and_risks(self):
        report = InterviewReport(
            dimensions=[], overall_score=75, strengths=[], improvements=[],
            overall_comment="", recommended_topics=[], next_step_advice="",
            evidence=["候选人解释了混合检索流程"], risk_flags=["并发规模待核验"],
        )
        self.assertEqual(len(report.evidence), 1)
        self.assertEqual(len(report.risk_flags), 1)

    def test_report_score_range_is_validated(self):
        with self.assertRaises(ValueError):
            InterviewReport(
                dimensions=[], overall_score=101, strengths=[], improvements=[],
                overall_comment="", recommended_topics=[], next_step_advice="",
            )


if __name__ == "__main__":
    unittest.main()
