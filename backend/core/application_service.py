"""招聘申请领域服务。

本模块只管理跨 Agent 的招聘业务状态。四个 Agent 仍负责生成各自的评估结果，
后续通过这里的方法绑定结果并推进申请。调用方负责提交或回滚数据库事务。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.exceptions import (
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationStateError,
)
from backend.core.logger import get_logger

logger = get_logger(__name__)


class ApplicationAction(str, Enum):
    UPLOAD_RESUME = "upload_resume"
    START_EXAM = "start_exam"
    VIEW_EXAM = "view_exam"
    SUBMIT_EXAM = "submit_exam"
    START_INTERVIEW = "start_interview"
    INTERVIEW_MESSAGE = "interview_message"


class ResumeDecision(str, Enum):
    PASSED = "passed"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"


class CandidateTask(BaseModel):
    """候选人可见的任务；不能加入分数、报告或筛选结论。"""

    type: str
    available: bool
    completed: bool
    open_at: datetime | None = None
    deadline: datetime | None = None


class CandidateTaskView(BaseModel):
    """候选人任务中心响应模型，不暴露招聘内部状态。"""

    application_id: str
    position_id: str
    position_title: str
    notice: str = "材料已提交，请等待 HR 通知"
    tasks: list[CandidateTask] = Field(default_factory=list)


class ApplicationSnapshot(BaseModel):
    """服务内部结果，供后续 Agent/API 串联；不得直接返回候选人。"""

    id: str
    tenant_id: str
    position_id: str
    candidate_id: str
    status: str
    resume_decision: str
    exam_status: str
    interview_status: str
    resume_review_id: str | None = None
    exam_submission_id: str | None = None
    interview_session_id: str | None = None
    exam_open_at: datetime | None = None
    exam_deadline: datetime | None = None
    interview_open_at: datetime | None = None
    interview_deadline: datetime | None = None


class ResumePositionContext(BaseModel):
    """简历 Agent 的内部岗位上下文，不得直接返回候选人。"""

    application_id: str
    position_id: str
    position_title: str
    job_description: str
    job_requirements: dict[str, Any] = Field(default_factory=dict)
    resume_pass_score: float


class ExamPositionContext(BaseModel):
    """笔试 Agent 的内部岗位上下文，不得直接返回候选人。"""

    application_id: str
    position_id: str
    position_title: str
    exam_id: str


class InterviewPositionContext(BaseModel):
    """面试 Agent 的内部岗位和简历上下文。"""

    application_id: str
    position_id: str
    position_title: str
    job_description: str
    job_requirements: dict[str, Any] = Field(default_factory=dict)
    resume_review_id: str


_SNAPSHOT_COLUMNS = """
    id, tenant_id, position_id, candidate_id, status, resume_decision,
    exam_status, interview_status, resume_review_id, exam_submission_id,
    interview_session_id, exam_open_at, exam_deadline,
    interview_open_at, interview_deadline
"""

_APPLICATION_RETURNING = """
    a.id AS id, a.tenant_id AS tenant_id, a.position_id AS position_id,
    a.candidate_id AS candidate_id, a.status AS status,
    a.resume_decision AS resume_decision, a.exam_status AS exam_status,
    a.interview_status AS interview_status,
    a.resume_review_id AS resume_review_id,
    a.exam_submission_id AS exam_submission_id,
    a.interview_session_id AS interview_session_id,
    a.exam_open_at AS exam_open_at, a.exam_deadline AS exam_deadline,
    a.interview_open_at AS interview_open_at,
    a.interview_deadline AS interview_deadline
"""


class ApplicationService:
    """在一个 AsyncSession 内执行招聘申请状态变更。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _snapshot(row: Any) -> ApplicationSnapshot:
        data = dict(row)
        for key in (
            "id", "tenant_id", "position_id", "candidate_id",
            "resume_review_id", "exam_submission_id", "interview_session_id",
        ):
            if data.get(key) is not None:
                data[key] = str(data[key])
        return ApplicationSnapshot.model_validate(data)

    async def _get_application(
        self,
        application_id: str,
        tenant_id: str,
        *,
        for_update: bool = False,
    ) -> Any:
        lock_clause = " FOR UPDATE" if for_update else ""
        result = await self.session.execute(
            text(
                f"""
                SELECT {_SNAPSHOT_COLUMNS}
                FROM recruitment_applications
                WHERE id = :application_id AND tenant_id = :tenant_id
                {lock_clause}
                """
            ),
            {"application_id": application_id, "tenant_id": tenant_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationNotFoundError(
                "招聘申请不存在",
                details={"application_id": application_id},
            )
        return row

    @staticmethod
    def _ensure_candidate(row: Any, candidate_id: str) -> None:
        if str(row["candidate_id"]) != str(candidate_id):
            raise ApplicationPermissionError("无权操作该招聘申请")

    @staticmethod
    def _within_window(
        now: datetime,
        open_at: datetime | None,
        deadline: datetime | None,
    ) -> bool:
        return (open_at is None or now >= open_at) and (
            deadline is None or now <= deadline
        )

    @classmethod
    def _validate_action(cls, row: Any, action: ApplicationAction) -> None:
        now = datetime.now(timezone.utc)

        if action == ApplicationAction.UPLOAD_RESUME:
            if row["status"] != "draft" or row["resume_review_id"] is not None:
                raise ApplicationStateError("当前申请不能重复上传简历")
            return

        if action in (
            ApplicationAction.START_EXAM,
            ApplicationAction.VIEW_EXAM,
            ApplicationAction.SUBMIT_EXAM,
        ):
            allowed_statuses = {
                ApplicationAction.START_EXAM: {"available"},
                ApplicationAction.VIEW_EXAM: {"available", "in_progress"},
                ApplicationAction.SUBMIT_EXAM: {"in_progress"},
            }[action]
            if row["exam_status"] not in allowed_statuses:
                raise ApplicationStateError("当前申请的笔试任务不可操作")
            if not cls._within_window(now, row["exam_open_at"], row["exam_deadline"]):
                raise ApplicationStateError("笔试不在可操作时间范围内")
            return

        if action in (
            ApplicationAction.START_INTERVIEW,
            ApplicationAction.INTERVIEW_MESSAGE,
        ):
            allowed_status = (
                "available"
                if action == ApplicationAction.START_INTERVIEW
                else "in_progress"
            )
            if row["interview_status"] != allowed_status:
                raise ApplicationStateError("当前申请的面试任务不可操作")
            if not cls._within_window(
                now,
                row["interview_open_at"],
                row["interview_deadline"],
            ):
                raise ApplicationStateError("面试不在可操作时间范围内")
            return

        raise ApplicationStateError(f"不支持的申请动作：{action.value}")

    async def create_application(
        self,
        *,
        candidate_id: str,
        position_id: str,
        tenant_id: str,
    ) -> ApplicationSnapshot:
        """候选人为开放岗位创建唯一申请。"""
        target = await self.session.execute(
            text("""
                SELECT p.id
                FROM job_positions p
                JOIN users u
                  ON u.id = :candidate_id
                 AND u.tenant_id = p.tenant_id
                 AND u.role = 'student'
                 AND u.is_active = TRUE
                WHERE p.id = :position_id
                  AND p.tenant_id = :tenant_id
                  AND p.status = 'open'
                  AND p.exam_id IS NOT NULL
                  AND (p.apply_deadline IS NULL OR NOW() <= p.apply_deadline)
            """),
            {
                "candidate_id": candidate_id,
                "position_id": position_id,
                "tenant_id": tenant_id,
            },
        )
        if not target.fetchone():
            raise ApplicationNotFoundError("岗位不存在、未开放或已停止申请")

        result = await self.session.execute(
            text(f"""
                INSERT INTO recruitment_applications
                    (tenant_id, position_id, candidate_id)
                VALUES (:tenant_id, :position_id, :candidate_id)
                ON CONFLICT (position_id, candidate_id) DO NOTHING
                RETURNING {_SNAPSHOT_COLUMNS}
            """),
            {
                "candidate_id": candidate_id,
                "position_id": position_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("该候选人已经申请过当前岗位")

        logger.info(
            "application.created",
            application_id=str(row["id"]),
            position_id=position_id,
            candidate_id=candidate_id,
        )
        return self._snapshot(row)

    async def assert_action_allowed(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        action: ApplicationAction,
        for_update: bool = False,
    ) -> ApplicationSnapshot:
        """验证申请归属、当前状态和任务时间窗口。"""
        row = await self._get_application(
            application_id,
            tenant_id,
            for_update=for_update,
        )
        self._ensure_candidate(row, candidate_id)
        self._validate_action(row, action)
        return self._snapshot(row)

    async def get_resume_position_context(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        for_update: bool = False,
    ) -> ResumePositionContext:
        """校验上传权限并加载简历评审所需的岗位 JD。"""
        snapshot = await self.assert_action_allowed(
            application_id=application_id,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            action=ApplicationAction.UPLOAD_RESUME,
            for_update=for_update,
        )
        result = await self.session.execute(
            text("""
                SELECT title, jd_text, requirements, resume_pass_score
                FROM job_positions
                WHERE id = :position_id AND tenant_id = :tenant_id
            """),
            {"position_id": snapshot.position_id, "tenant_id": tenant_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationNotFoundError("招聘岗位不存在")
        return ResumePositionContext(
            application_id=snapshot.id,
            position_id=snapshot.position_id,
            position_title=row["title"],
            job_description=row["jd_text"],
            job_requirements=row["requirements"] or {},
            resume_pass_score=float(row["resume_pass_score"]),
        )

    async def attach_resume_review(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        review_id: str,
    ) -> ApplicationSnapshot:
        """绑定已上传的简历，并立即开放笔试和初面；AI 评审不阻塞流程。"""
        await self.assert_action_allowed(
            application_id=application_id,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            action=ApplicationAction.UPLOAD_RESUME,
            for_update=True,
        )
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET resume_review_id = :review_id,
                    status = 'screening',
                    exam_status = 'available',
                    interview_status = 'available',
                    exam_open_at = NOW(),
                    exam_deadline = NOW() + p.exam_window_hours * INTERVAL '1 hour',
                    interview_open_at = NOW(),
                    interview_deadline = NOW() + p.interview_window_hours * INTERVAL '1 hour',
                    updated_at = NOW()
                FROM resume_reviews AS r, job_positions AS p
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.candidate_id = :candidate_id
                  AND a.status = 'draft'
                  AND a.resume_review_id IS NULL
                  AND r.id = :review_id
                  AND r.tenant_id = a.tenant_id
                  AND r.student_id = a.candidate_id
                  AND p.id = a.position_id
                  AND p.tenant_id = a.tenant_id
                RETURNING {_APPLICATION_RETURNING}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
                "review_id": review_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("简历记录无效或申请状态已经变化")
        return self._snapshot(row)

    async def apply_resume_result(
        self,
        *,
        application_id: str,
        tenant_id: str,
        review_id: str,
        decision: ResumeDecision | str,
    ) -> ApplicationSnapshot:
        """回写简历评审结论，仅供招聘方参考，不再控制任务开放。"""
        try:
            decision = ResumeDecision(decision)
        except ValueError as exc:
            raise ApplicationStateError("无效的简历评审结论") from exc

        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET resume_decision = :decision, updated_at = NOW()
                FROM resume_reviews AS r
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.position_id = p.id
                  AND a.resume_review_id = :review_id
                  AND r.id = a.resume_review_id
                  AND r.tenant_id = a.tenant_id
                  AND r.student_id = a.candidate_id
                  AND r.status = 'done'
                  AND a.status IN ('completed', 'screening')
                  AND a.resume_decision IN ('pending', 'manual_review')
                RETURNING {_APPLICATION_RETURNING}
            """),
            {
                "application_id": application_id,
                "tenant_id": tenant_id,
                "review_id": review_id,
                "decision": decision.value,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("简历结果无法应用，申请状态或评审记录不匹配")

        logger.info(
            "application.resume_result_applied",
            application_id=application_id,
            decision=decision.value,
        )
        return self._snapshot(row)

    async def reset_resume_after_failure(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        review_id: str,
    ) -> ApplicationSnapshot:
        """简历处理失败后解绑失败记录，允许候选人重新上传。"""
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET status = 'draft',
                    resume_decision = 'pending',
                    resume_review_id = NULL,
                    exam_status = 'locked',
                    interview_status = 'locked',
                    exam_open_at = NULL,
                    exam_deadline = NULL,
                    interview_open_at = NULL,
                    interview_deadline = NULL,
                    updated_at = NOW()
                FROM resume_reviews AS r
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.candidate_id = :candidate_id
                  AND a.status = 'screening'
                  AND a.resume_review_id = :review_id
                  AND r.id = a.resume_review_id
                  AND r.tenant_id = a.tenant_id
                  AND r.student_id = a.candidate_id
                  AND r.status = 'failed'
                RETURNING {_APPLICATION_RETURNING}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
                "review_id": review_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("简历任务未失败、记录不匹配或已经恢复")
        return self._snapshot(row)

    async def start_exam(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
    ) -> ApplicationSnapshot:
        """将已开放的笔试任务原子推进为进行中。"""
        await self.assert_action_allowed(
            application_id=application_id,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            action=ApplicationAction.START_EXAM,
        )
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications
                SET exam_status = 'in_progress', updated_at = NOW()
                WHERE id = :application_id
                  AND tenant_id = :tenant_id
                  AND candidate_id = :candidate_id
                  AND exam_status = 'available'
                  AND (exam_open_at IS NULL OR NOW() >= exam_open_at)
                  AND (exam_deadline IS NULL OR NOW() <= exam_deadline)
                RETURNING {_SNAPSHOT_COLUMNS}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("笔试任务已被处理或当前不可开始")
        return self._snapshot(row)

    async def get_exam_position_context(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        for_update: bool = False,
        action: ApplicationAction = ApplicationAction.START_EXAM,
    ) -> ExamPositionContext:
        """校验笔试是否可开始，并加载岗位绑定的试卷。"""
        snapshot = await self.assert_action_allowed(
            application_id=application_id,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            action=action,
            for_update=for_update,
        )
        result = await self.session.execute(
            text("""
                SELECT title, exam_id
                FROM job_positions
                WHERE id = :position_id
                  AND tenant_id = :tenant_id
                  AND exam_id IS NOT NULL
            """),
            {"position_id": snapshot.position_id, "tenant_id": tenant_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationNotFoundError("招聘岗位未配置笔试")
        return ExamPositionContext(
            application_id=snapshot.id,
            position_id=snapshot.position_id,
            position_title=row["title"],
            exam_id=str(row["exam_id"]),
        )

    async def record_exam_result(
        self,
        *,
        application_id: str,
        tenant_id: str,
        submission_id: str,
        needs_review: bool,
    ) -> ApplicationSnapshot:
        """绑定笔试提交；需复核时暂停，否则标记完成。"""
        exam_status = "pending_review" if needs_review else "completed"
        submission_status = "pending_review" if needs_review else "published"
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET exam_submission_id = s.id,
                    exam_status = CAST(:exam_status AS VARCHAR(24)),
                    status = CASE
                        WHEN CAST(:exam_status AS VARCHAR(24)) = 'completed'
                         AND a.interview_status = 'completed' THEN 'completed'
                        ELSE a.status
                    END,
                    updated_at = NOW()
                FROM exam_submissions AS s, job_positions AS p
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.position_id = p.id
                  AND s.id = :submission_id
                  AND s.tenant_id = a.tenant_id
                  AND s.student_id = a.candidate_id
                  AND s.exam_id = p.exam_id
                  AND s.status = :submission_status
                  AND a.exam_status IN ('in_progress', 'pending_review')
                RETURNING {_APPLICATION_RETURNING}
            """),
            {
                "application_id": application_id,
                "tenant_id": tenant_id,
                "submission_id": submission_id,
                "exam_status": exam_status,
                "submission_status": submission_status,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("笔试提交与申请不匹配或状态不可更新")
        return self._snapshot(row)

    async def reset_exam_after_failure(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
    ) -> ApplicationSnapshot:
        """笔试尚未绑定结果且执行失败时，恢复为可开始状态。"""
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications
                SET exam_status = 'available', updated_at = NOW()
                WHERE id = :application_id
                  AND tenant_id = :tenant_id
                  AND candidate_id = :candidate_id
                  AND exam_status = 'in_progress'
                  AND exam_submission_id IS NULL
                  AND (exam_deadline IS NULL OR NOW() <= exam_deadline)
                RETURNING {_SNAPSHOT_COLUMNS}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("笔试任务不能恢复或已经超过截止时间")
        return self._snapshot(row)

    async def start_interview(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
    ) -> ApplicationSnapshot:
        """将已开放的面试任务原子推进为进行中。"""
        await self.assert_action_allowed(
            application_id=application_id,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            action=ApplicationAction.START_INTERVIEW,
        )
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications
                SET interview_status = 'in_progress', updated_at = NOW()
                WHERE id = :application_id
                  AND tenant_id = :tenant_id
                  AND candidate_id = :candidate_id
                  AND interview_status = 'available'
                  AND (interview_open_at IS NULL OR NOW() >= interview_open_at)
                  AND (interview_deadline IS NULL OR NOW() <= interview_deadline)
                RETURNING {_SNAPSHOT_COLUMNS}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("面试任务已被处理或当前不可开始")
        return self._snapshot(row)

    async def get_interview_position_context(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        for_update: bool = False,
    ) -> InterviewPositionContext:
        """校验面试任务，并加载岗位 JD 及已上传的简历。"""
        snapshot = await self.assert_action_allowed(
            application_id=application_id,
            candidate_id=candidate_id,
            tenant_id=tenant_id,
            action=ApplicationAction.START_INTERVIEW,
            for_update=for_update,
        )
        result = await self.session.execute(
            text("""
                SELECT p.title, p.jd_text, p.requirements, a.resume_review_id
                FROM recruitment_applications a
                JOIN job_positions p
                  ON p.id = a.position_id AND p.tenant_id = a.tenant_id
                JOIN resume_reviews r
                  ON r.id = a.resume_review_id
                 AND r.tenant_id = a.tenant_id
                 AND r.student_id = a.candidate_id
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.candidate_id = :candidate_id
            """),
            {"application_id": application_id, "tenant_id": tenant_id,
             "candidate_id": candidate_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("面试缺少已上传的简历或岗位上下文")
        return InterviewPositionContext(
            application_id=snapshot.id,
            position_id=snapshot.position_id,
            position_title=row["title"],
            job_description=row["jd_text"],
            job_requirements=row["requirements"] or {},
            resume_review_id=str(row["resume_review_id"]),
        )

    async def attach_interview_session(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        session_id: str,
    ) -> ApplicationSnapshot:
        """把面试 Agent 的公开 session_id 绑定到招聘申请。"""
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET interview_session_id = i.id, updated_at = NOW()
                FROM interview_sessions AS i
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.candidate_id = :candidate_id
                  AND a.interview_status = 'in_progress'
                  AND a.interview_session_id IS NULL
                  AND i.session_id = :session_id
                  AND i.tenant_id = a.tenant_id
                  AND i.student_id = a.candidate_id
                RETURNING {_APPLICATION_RETURNING}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("面试会话与申请不匹配或已经绑定")
        return self._snapshot(row)

    async def reset_interview_after_failure(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
    ) -> ApplicationSnapshot:
        """面试会话创建前失败时，恢复为可开始状态。"""
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications
                SET interview_status = 'available', updated_at = NOW()
                WHERE id = :application_id
                  AND tenant_id = :tenant_id
                  AND candidate_id = :candidate_id
                  AND interview_status = 'in_progress'
                  AND interview_session_id IS NULL
                  AND (interview_deadline IS NULL OR NOW() <= interview_deadline)
                RETURNING {_SNAPSHOT_COLUMNS}
            """),
            {
                "application_id": application_id,
                "candidate_id": candidate_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("面试任务不能恢复或已经超过截止时间")
        return self._snapshot(row)

    async def reset_interview_start_after_failure(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
        session_id: str,
    ) -> ApplicationSnapshot:
        """首轮生成失败时解绑未完成会话，并恢复面试任务。"""
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET interview_status = 'available',
                    interview_session_id = NULL,
                    updated_at = NOW()
                FROM interview_sessions AS i
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.candidate_id = :candidate_id
                  AND a.interview_status = 'in_progress'
                  AND a.interview_session_id = i.id
                  AND i.session_id = :session_id
                  AND i.tenant_id = a.tenant_id
                  AND i.student_id = a.candidate_id
                  AND i.status = 'in_progress'
                RETURNING {_APPLICATION_RETURNING}
            """),
            {"application_id": application_id, "candidate_id": candidate_id,
             "tenant_id": tenant_id, "session_id": session_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("面试会话不能恢复或状态已经变化")
        return self._snapshot(row)

    async def complete_interview(
        self,
        *,
        application_id: str,
        tenant_id: str,
        session_id: str,
    ) -> ApplicationSnapshot:
        """面试报告落库后推进申请；不会把报告暴露给候选人。"""
        result = await self.session.execute(
            text(f"""
                UPDATE recruitment_applications AS a
                SET interview_status = 'completed',
                    status = CASE
                        WHEN a.exam_status = 'completed' THEN 'completed'
                        ELSE a.status
                    END,
                    updated_at = NOW()
                FROM interview_sessions AS i
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
                  AND a.interview_session_id = i.id
                  AND i.session_id = :session_id
                  AND i.tenant_id = a.tenant_id
                  AND i.status = 'finished'
                  AND a.interview_status = 'in_progress'
                RETURNING {_APPLICATION_RETURNING}
            """),
            {
                "application_id": application_id,
                "tenant_id": tenant_id,
                "session_id": session_id,
            },
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationStateError("面试会话尚未完成或与申请不匹配")
        return self._snapshot(row)

    async def assert_recruiter_access(
        self,
        *,
        application_id: str,
        tenant_id: str,
        actor_role: str,
    ) -> ApplicationSnapshot:
        """招聘报告读取前的统一权限检查。"""
        if actor_role not in ("teacher", "admin", "hr", "recruiter"):
            raise ApplicationPermissionError("只有招聘官或管理员可以查看评估结果")
        row = await self._get_application(application_id, tenant_id)
        return self._snapshot(row)

    async def get_candidate_tasks(
        self,
        *,
        application_id: str,
        candidate_id: str,
        tenant_id: str,
    ) -> CandidateTaskView:
        """返回候选人任务，不返回任何评估结果或内部决策。"""
        result = await self.session.execute(
            text("""
                SELECT a.id, a.candidate_id, a.position_id,
                       a.status, a.resume_review_id,
                       a.exam_status, a.interview_status,
                       a.exam_open_at, a.exam_deadline,
                       a.interview_open_at, a.interview_deadline,
                       p.title AS position_title
                FROM recruitment_applications AS a
                JOIN job_positions AS p ON p.id = a.position_id
                WHERE a.id = :application_id
                  AND a.tenant_id = :tenant_id
            """),
            {"application_id": application_id, "tenant_id": tenant_id},
        )
        row = result.mappings().fetchone()
        if not row:
            raise ApplicationNotFoundError("招聘申请不存在")
        self._ensure_candidate(row, candidate_id)

        tasks = self._build_candidate_tasks(row)

        available_tasks = [task for task in tasks if task.available and not task.completed]
        if available_tasks:
            notice = "请在规定时间内完成已开放任务"
        elif row["resume_review_id"] is None:
            notice = "请先提交简历"
        else:
            notice = "材料已提交，请等待 HR 通知"

        return CandidateTaskView(
            application_id=str(row["id"]),
            position_id=str(row["position_id"]),
            position_title=row["position_title"],
            notice=notice,
            tasks=tasks,
        )

    @classmethod
    def _build_candidate_tasks(
        cls,
        row: Any,
        *,
        now: datetime | None = None,
    ) -> list[CandidateTask]:
        """从内部状态生成候选人安全任务，不暴露筛选决定。"""
        now = now or datetime.now(timezone.utc)
        tasks: list[CandidateTask] = []

        resume_submitted = row.get("resume_review_id") is not None
        tasks.append(CandidateTask(
            type="resume",
            available=(row.get("status") == "draft" and not resume_submitted),
            completed=resume_submitted,
        ))

        task_specs = (
            (
                "exam", row["exam_status"],
                row["exam_open_at"], row["exam_deadline"],
            ),
            (
                "interview", row["interview_status"],
                row["interview_open_at"], row["interview_deadline"],
            ),
        )
        for task_type, task_status, open_at, deadline in task_specs:
            # locked/expired 不返回，避免通过内部状态推断筛选结论。
            if task_status not in ("available", "in_progress", "pending_review", "completed"):
                continue
            tasks.append(CandidateTask(
                type=task_type,
                available=(
                    task_status in ("available", "in_progress")
                    and cls._within_window(now, open_at, deadline)
                ),
                # 候选人只看到“已提交”，不暴露内部人工复核状态。
                completed=task_status in ("pending_review", "completed"),
                open_at=open_at,
                deadline=deadline,
            ))

        return tasks
