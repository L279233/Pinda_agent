from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from backend.core.application_service import (
    ApplicationAction,
    ApplicationService,
    CandidateTask,
    CandidateTaskView,
)
from backend.core.exceptions import ApplicationStateError


def _application_row(**overrides):
    row = {
        "status": "draft",
        "resume_review_id": None,
        "exam_status": "locked",
        "interview_status": "locked",
        "exam_open_at": None,
        "exam_deadline": None,
        "interview_open_at": None,
        "interview_deadline": None,
    }
    row.update(overrides)
    return row


class ApplicationServiceRulesTest(unittest.TestCase):
    def test_resume_upload_is_allowed_only_for_draft_application(self):
        ApplicationService._validate_action(
            _application_row(),
            ApplicationAction.UPLOAD_RESUME,
        )

        with self.assertRaises(ApplicationStateError):
            ApplicationService._validate_action(
                _application_row(
                    status="resume_processing",
                    resume_review_id="review-1",
                ),
                ApplicationAction.UPLOAD_RESUME,
            )

    def test_exam_start_requires_available_status_and_valid_window(self):
        now = datetime.now(timezone.utc)
        ApplicationService._validate_action(
            _application_row(
                exam_status="available",
                exam_open_at=now - timedelta(minutes=1),
                exam_deadline=now + timedelta(hours=1),
            ),
            ApplicationAction.START_EXAM,
        )

        with self.assertRaises(ApplicationStateError):
            ApplicationService._validate_action(
                _application_row(
                    exam_status="available",
                    exam_open_at=now - timedelta(hours=2),
                    exam_deadline=now - timedelta(hours=1),
                ),
                ApplicationAction.START_EXAM,
            )

    def test_exam_view_allows_available_and_in_progress(self):
        for exam_status in ("available", "in_progress"):
            with self.subTest(exam_status=exam_status):
                ApplicationService._validate_action(
                    _application_row(exam_status=exam_status),
                    ApplicationAction.VIEW_EXAM,
                )

    def test_exam_submit_requires_in_progress(self):
        ApplicationService._validate_action(
            _application_row(exam_status="in_progress"),
            ApplicationAction.SUBMIT_EXAM,
        )

        with self.assertRaises(ApplicationStateError):
            ApplicationService._validate_action(
                _application_row(exam_status="available"),
                ApplicationAction.SUBMIT_EXAM,
            )

    def test_interview_message_requires_in_progress_status(self):
        with self.assertRaises(ApplicationStateError):
            ApplicationService._validate_action(
                _application_row(interview_status="available"),
                ApplicationAction.INTERVIEW_MESSAGE,
            )

    def test_window_boundaries_are_inclusive(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(ApplicationService._within_window(now, now, now))

    def test_candidate_task_view_contains_no_evaluation_fields(self):
        view = CandidateTaskView(
            application_id="application-1",
            position_id="position-1",
            position_title="大模型应用开发工程师",
            tasks=[CandidateTask(type="exam", available=True, completed=False)],
        )

        payload = view.model_dump()
        forbidden = {
            "status", "resume_decision", "score", "report",
            "final_decision", "ranking", "weighted_score",
        }
        self.assertTrue(forbidden.isdisjoint(payload.keys()))
        self.assertTrue(forbidden.isdisjoint(payload["tasks"][0].keys()))

    def test_candidate_tasks_include_resume_without_exposing_decision(self):
        tasks = ApplicationService._build_candidate_tasks({
            "status": "draft",
            "resume_review_id": None,
            "exam_status": "locked",
            "interview_status": "locked",
            "exam_open_at": None,
            "exam_deadline": None,
            "interview_open_at": None,
            "interview_deadline": None,
        })

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].type, "resume")
        self.assertTrue(tasks[0].available)
        self.assertFalse(tasks[0].completed)
        self.assertNotIn("decision", tasks[0].model_dump())

    def test_locked_tasks_are_hidden_after_resume_submission(self):
        tasks = ApplicationService._build_candidate_tasks({
            "status": "rejected",
            "resume_review_id": "review-1",
            "exam_status": "locked",
            "interview_status": "locked",
            "exam_open_at": None,
            "exam_deadline": None,
            "interview_open_at": None,
            "interview_deadline": None,
        })

        self.assertEqual([task.type for task in tasks], ["resume"])
        self.assertTrue(tasks[0].completed)

    def test_uploaded_resume_exposes_exam_and_interview_tasks(self):
        now = datetime.now(timezone.utc)
        tasks = ApplicationService._build_candidate_tasks({
            "status": "screening",
            "resume_review_id": "review-1",
            "exam_status": "available",
            "interview_status": "available",
            "exam_open_at": now - timedelta(minutes=1),
            "exam_deadline": now + timedelta(hours=24),
            "interview_open_at": now - timedelta(minutes=1),
            "interview_deadline": now + timedelta(hours=24),
        }, now=now)

        self.assertEqual([task.type for task in tasks], ["resume", "exam", "interview"])
        self.assertTrue(tasks[0].completed)
        self.assertTrue(tasks[1].available)
        self.assertTrue(tasks[2].available)

    def test_recruitment_constraints_exist_in_init_and_migrations(self):
        project_root = Path(__file__).resolve().parents[2]
        init_sql = (project_root / "scripts" / "init_db.sql").read_text(encoding="utf-8")
        migrations = (
            project_root / "backend" / "db" / "migrations.py"
        ).read_text(encoding="utf-8")
        required_objects = {
            "ck_job_positions_open_exam",
            "uq_job_positions_exam_id",
            "uq_recruitment_applications_resume_review",
            "uq_recruitment_applications_exam_submission",
            "uq_recruitment_applications_interview_session",
        }
        for name in required_objects:
            self.assertIn(name, init_sql)
            self.assertIn(name, migrations)


class ApplicationServiceAsyncRulesTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_resume_decision_is_rejected_before_database_access(self):
        service = ApplicationService(session=None)
        with self.assertRaises(ApplicationStateError):
            await service.apply_resume_result(
                application_id="application-1",
                tenant_id="tenant-1",
                review_id="review-1",
                decision="unexpected",
            )


if __name__ == "__main__":
    unittest.main()
