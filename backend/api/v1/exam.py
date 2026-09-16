import asyncio
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.agents.exam.graph import build_exam_graph
from backend.core.application_service import ApplicationAction, ApplicationService
from backend.core.exceptions import (
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationStateError,
)
from backend.core.logger import get_logger
from backend.core.memory import build_config
from backend.dependencies import AsyncSessionLocal, get_current_user

router = APIRouter()
logger = get_logger(__name__)
_graph = build_exam_graph()
_background_tasks: set[asyncio.Task] = set()
RECRUITER_ROLES = {"teacher", "admin", "hr", "recruiter"}


class OnlineAnswer(BaseModel):
    question_id: uuid.UUID
    answer: str = Field(default="", max_length=20000)


class OnlineExamSubmitRequest(BaseModel):
    application_id: uuid.UUID
    answers: list[OnlineAnswer] = Field(..., min_length=1, max_length=200)


class OnlineQuestionResponse(BaseModel):
    question_id: str
    question_no: int
    question_type: str
    content: str
    score: int


class OnlineExamResponse(BaseModel):
    application_id: str
    position_title: str
    questions: list[OnlineQuestionResponse]


def _require_candidate(current_user: dict) -> None:
    if current_user.get("role") != "student":
        raise HTTPException(status_code=403, detail="仅候选人可以提交笔试")


def _require_recruiter(current_user: dict) -> None:
    if current_user.get("role") not in RECRUITER_ROLES:
        raise HTTPException(status_code=403, detail="仅招聘人员可以查看或复核笔试")


def _raise_application_http_error(exc: Exception) -> None:
    if isinstance(exc, ApplicationNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ApplicationPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ApplicationStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _candidate_submission_payload(submission_id: str, db_status: str) -> dict:
    """候选人响应不暴露成绩、逐题反馈或人工复核状态。"""
    if db_status == "submitted":
        return {
            "submission_id": submission_id,
            "status": "failed",
            "message": "笔试处理失败，请重新提交",
            "retry_allowed": True,
        }
    if db_status in {"ai_processing", "pending_review", "reviewed"}:
        return {"submission_id": submission_id, "status": "processing", "message": "笔试已提交"}
    return {"submission_id": submission_id, "status": "submitted", "message": "笔试已完成，请等待 HR 通知"}


async def _recover_failed_exam(
    *, application_id: str, candidate_id: str, tenant_id: str, submission_id: str
) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await session.execute(
                text("""
                    UPDATE exam_submissions
                    SET status = 'submitted', updated_at = NOW()
                    WHERE id = :submission_id
                      AND tenant_id = :tenant_id
                      AND student_id = :candidate_id
                      AND status = 'ai_processing'
                """),
                {"submission_id": submission_id, "tenant_id": tenant_id,
                 "candidate_id": candidate_id},
            )
            await ApplicationService(session).reset_exam_after_failure(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("exam.failure_recovery_failed", submission_id=submission_id, error=str(exc))


def _initial_exam_state(
    *, context, submission_id: str, candidate_id: str, tenant_id: str,
    source: str, parsed_questions: list[dict], word_file_path: str = "",
) -> dict:
    return {
        "messages": [], "student_id": candidate_id, "tenant_id": tenant_id,
        "session_id": submission_id, "application_id": context.application_id,
        "position_id": context.position_id, "position_title": context.position_title,
        "exam_id": context.exam_id, "submission_id": submission_id,
        "submission_source": source, "word_file_path": word_file_path,
        "parsed_questions": parsed_questions, "objective_results": [],
        "subjective_results": [], "code_results": [], "pre_review_summary": {},
        "weak_points": [], "weak_points_summary": "", "teacher_decision": None,
        "final_results": [], "structured_output": None, "fallback_used": False,
        "sandbox_skipped": False, "teacher_notified": False, "published": False,
    }


def _launch_exam_graph(
    *, initial_state: dict, application_id: str, candidate_id: str,
    tenant_id: str, submission_id: str, cleanup_path: str | None = None,
) -> None:
    config = build_config(candidate_id, submission_id, "exam")

    def _on_task_done(task: asyncio.Task) -> None:
        _background_tasks.discard(task)
        failed = task.cancelled() or task.exception() is not None
        if failed:
            error = "cancelled" if task.cancelled() else str(task.exception())
            logger.error("exam.background_task_failed", submission_id=submission_id, error=error)

        async def cleanup() -> None:
            if failed:
                await _recover_failed_exam(
                    application_id=application_id, candidate_id=candidate_id,
                    tenant_id=tenant_id, submission_id=submission_id,
                )
            if cleanup_path and os.path.exists(cleanup_path):
                try:
                    os.remove(cleanup_path)
                except OSError:
                    pass

        cleanup_task = asyncio.create_task(cleanup())
        _background_tasks.add(cleanup_task)
        cleanup_task.add_done_callback(_background_tasks.discard)

    task = asyncio.create_task(_graph.ainvoke(initial_state, config=config))
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)


@router.get("/online/{application_id}", response_model=OnlineExamResponse)
async def get_online_exam(
    application_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
):
    """开始或继续在线笔试；仅返回题面，不返回答案和评分点。"""
    _require_candidate(current_user)
    aid = str(application_id)
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                service = ApplicationService(session)
                context = await service.get_exam_position_context(
                    application_id=aid, candidate_id=current_user["user_id"],
                    tenant_id=current_user["tenant_id"], for_update=True,
                    action=ApplicationAction.VIEW_EXAM,
                )
                snapshot = await service.assert_action_allowed(
                    application_id=aid, candidate_id=current_user["user_id"],
                    tenant_id=current_user["tenant_id"],
                    action=ApplicationAction.VIEW_EXAM, for_update=True,
                )
                if snapshot.exam_status == "available":
                    await service.start_exam(
                        application_id=aid, candidate_id=current_user["user_id"],
                        tenant_id=current_user["tenant_id"],
                    )
                rows = (await session.execute(text("""
                    SELECT id, question_no, question_type, content, score
                    FROM questions
                    WHERE exam_id = :exam_id AND tenant_id = :tenant_id
                    ORDER BY question_no
                """), {"exam_id": context.exam_id,
                       "tenant_id": current_user["tenant_id"]})).mappings().all()
            if not rows:
                raise HTTPException(status_code=409, detail="岗位笔试尚未配置题目")
            return {
                "application_id": aid, "position_title": context.position_title,
                "questions": [{
                    "question_id": str(row["id"]), "question_no": row["question_no"],
                    "question_type": row["question_type"], "content": row["content"],
                    "score": row["score"],
                } for row in rows],
            }
    except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
        _raise_application_http_error(exc)


@router.post("/online/submit", status_code=202)
async def submit_online_exam(
    req: OnlineExamSubmitRequest,
    current_user: dict = Depends(get_current_user),
):
    """提交 JSON 答案，后续复用原三轨批改图。"""
    _require_candidate(current_user)
    aid = str(req.application_id)
    candidate_id = current_user["user_id"]
    tenant_id = current_user["tenant_id"]
    answer_map = {str(item.question_id): item.answer.strip() for item in req.answers}
    if len(answer_map) != len(req.answers):
        raise HTTPException(status_code=400, detail="题目答案不能重复提交")
    submission_id = str(uuid.uuid4())

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                service = ApplicationService(session)
                context = await service.get_exam_position_context(
                    application_id=aid, candidate_id=candidate_id,
                    tenant_id=tenant_id, for_update=True,
                    action=ApplicationAction.SUBMIT_EXAM,
                )
                questions = (await session.execute(text("""
                    SELECT id, question_no FROM questions
                    WHERE exam_id = :exam_id AND tenant_id = :tenant_id
                    ORDER BY question_no
                """), {"exam_id": context.exam_id, "tenant_id": tenant_id})).mappings().all()
                expected = {str(row["id"]) for row in questions}
                if not expected or set(answer_map) != expected:
                    raise HTTPException(status_code=400, detail="答案题目必须与当前岗位试卷完全一致")
                # Failed async runs are reset to the legacy `submitted` marker.
                # Remove only that retryable marker; published/processing runs remain protected.
                await session.execute(text("""
                    DELETE FROM exam_submissions
                    WHERE exam_id = :exam_id AND student_id = :student_id
                      AND tenant_id = :tenant_id AND status = 'submitted'
                """), {"exam_id": context.exam_id, "student_id": candidate_id, "tenant_id": tenant_id})
                await session.execute(text("""
                    INSERT INTO exam_submissions
                        (id, tenant_id, exam_id, student_id, source, status, submitted_at)
                    VALUES (:id, :tenant_id, :exam_id, :student_id, 'online', 'ai_processing', NOW())
                """), {"id": submission_id, "tenant_id": tenant_id,
                       "exam_id": context.exam_id, "student_id": candidate_id})
    except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
        _raise_application_http_error(exc)
    except IntegrityError as exc:
        # exam_id + student_id is unique: concurrent requests share one result.
        raise HTTPException(status_code=409, detail="该笔试已经提交，请等待 HR 通知") from exc

    parsed = [{"question_no": row["question_no"], "student_answer": answer_map[str(row["id"])]} for row in questions]
    initial_state = _initial_exam_state(
        context=context, submission_id=submission_id, candidate_id=candidate_id,
        tenant_id=tenant_id, source="online", parsed_questions=parsed,
    )
    _launch_exam_graph(
        initial_state=initial_state, application_id=aid, candidate_id=candidate_id,
        tenant_id=tenant_id, submission_id=submission_id,
    )
    return _candidate_submission_payload(submission_id, "ai_processing")


@router.post("/submit", status_code=202)
async def submit_exam(
    application_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """提交招聘笔试 Word 文件；试卷由申请岗位决定，客户端不能指定。"""
    _require_candidate(current_user)
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 格式")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大支持 20MB")

    application_id = str(application_id)
    submission_id = str(uuid.uuid4())
    candidate_id = current_user["user_id"]
    tenant_id = current_user["tenant_id"]
    tmp_path = os.path.join(tempfile.gettempdir(), f"{submission_id}.docx")

    try:
        async with AsyncSessionLocal() as session:
            service = ApplicationService(session)
            context = await service.get_exam_position_context(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                for_update=True,
            )
            await session.execute(
                text("""
                    DELETE FROM exam_submissions
                    WHERE exam_id = :exam_id AND student_id = :student_id
                      AND tenant_id = :tenant_id AND status = 'submitted'
                """),
                {"exam_id": context.exam_id, "student_id": candidate_id, "tenant_id": tenant_id},
            )
            await session.execute(
                text("""
                    INSERT INTO exam_submissions
                        (id, tenant_id, exam_id, student_id, source, status, submitted_at)
                    VALUES (:id, :tenant_id, :exam_id, :student_id, 'word', 'ai_processing', NOW())
                """),
                {"id": submission_id, "tenant_id": tenant_id,
                 "exam_id": context.exam_id, "student_id": candidate_id},
            )
            await service.start_exam(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
            )
            await session.commit()
    except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
        _raise_application_http_error(exc)

    try:
        with open(tmp_path, "wb") as handle:
            handle.write(content)
    except Exception:
        await _recover_failed_exam(
            application_id=application_id, candidate_id=candidate_id,
            tenant_id=tenant_id, submission_id=submission_id,
        )
        raise HTTPException(status_code=500, detail="笔试文件保存失败，请重新提交")

    initial_state = _initial_exam_state(
        context=context, submission_id=submission_id, candidate_id=candidate_id,
        tenant_id=tenant_id, source="word", parsed_questions=[], word_file_path=tmp_path,
    )
    _launch_exam_graph(
        initial_state=initial_state, application_id=application_id,
        candidate_id=candidate_id, tenant_id=tenant_id,
        submission_id=submission_id, cleanup_path=tmp_path,
    )
    return _candidate_submission_payload(submission_id, "ai_processing")


@router.get("/my-submissions")
async def list_my_submissions(current_user: dict = Depends(get_current_user)):
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT es.id, es.status, es.submitted_at, p.title AS position_title
                    FROM exam_submissions es
                    JOIN job_positions p ON p.exam_id = es.exam_id AND p.tenant_id = es.tenant_id
                    JOIN recruitment_applications a
                      ON a.position_id = p.id AND a.candidate_id = es.student_id
                    WHERE es.student_id = :student_id AND es.tenant_id = :tenant_id
                    ORDER BY es.submitted_at DESC LIMIT 20
                """),
                {"student_id": current_user["user_id"], "tenant_id": current_user["tenant_id"]},
            )
        ).mappings().all()
    return {"items": [
        {**_candidate_submission_payload(str(row["id"]), row["status"]),
         "position_title": row["position_title"],
         "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None}
        for row in rows
    ]}


@router.get("/my-submissions/{submission_id}")
async def get_my_submission(submission_id: uuid.UUID, current_user: dict = Depends(get_current_user)):
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("""
                    SELECT id, status FROM exam_submissions
                    WHERE id = :id AND student_id = :student_id AND tenant_id = :tenant_id
                """),
                {"id": str(submission_id), "student_id": current_user["user_id"],
                 "tenant_id": current_user["tenant_id"]},
            )
        ).mappings().fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="提交记录不存在")
    return _candidate_submission_payload(str(submission_id), row["status"])


async def _get_thread_id(submission_id: str, tenant_id: str) -> str:
    from backend.core.memory import build_thread_id
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT student_id FROM exam_submissions WHERE id=:id AND tenant_id=:tenant_id"),
                {"id": submission_id, "tenant_id": tenant_id},
            )
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="笔试提交不存在")
    return f"exam:{build_thread_id(str(row[0]), submission_id)}"


async def _load_persisted_review(submission_id: str, tenant_id: str) -> dict | None:
    """Restore a pending review from PostgreSQL when the graph process was restarted."""
    async with AsyncSessionLocal() as session:
        submission = (await session.execute(text("""
            SELECT es.student_id, es.weak_points, es.weak_points_summary
            FROM exam_submissions es
            WHERE es.id=:id AND es.tenant_id=:tenant_id AND es.status='pending_review'
        """), {"id": submission_id, "tenant_id": tenant_id})).mappings().fetchone()
        if not submission:
            return None
        rows = (await session.execute(text("""
            SELECT er.question_id, q.question_no, er.question_type, q.score AS full_score,
                   q.content, q.correct_answer, er.student_answer, er.ai_score AS score,
                   er.ai_feedback, er.needs_review, er.ai_raw_result
            FROM exam_reviews er
            JOIN questions q ON q.id=er.question_id
            WHERE er.submission_id=:id ORDER BY q.question_no
        """), {"id": submission_id})).mappings().all()
    if not rows:
        return None
    by_question = []
    for row in rows:
        item = dict(row.get("ai_raw_result") or {})
        item.update({key: row[key] for key in (
            "question_id", "question_no", "question_type", "full_score", "content",
            "correct_answer", "student_answer", "score", "ai_feedback", "needs_review"
        )})
        item["question_id"] = str(item["question_id"])
        by_question.append(item)
    return {
        "submission_id": submission_id, "student_id": str(submission["student_id"]),
        "pre_review_summary": {
            "total_score": sum(item.get("score", 0) or 0 for item in by_question),
            "full_score": sum(item.get("full_score", 0) or 0 for item in by_question),
            "needs_review_count": sum(bool(item.get("needs_review")) for item in by_question),
            "by_question": by_question,
        },
        "weak_points": submission["weak_points"] or [],
        "weak_points_summary": submission["weak_points_summary"] or "",
    }


@router.get("/pending-reviews")
async def get_pending_reviews(current_user: dict = Depends(get_current_user)):
    _require_recruiter(current_user)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT es.id, u.username AS candidate_name, e.title AS exam_title,
                           p.title AS position_title, es.submitted_at
                    FROM exam_submissions es
                    JOIN users u ON u.id = es.student_id
                    JOIN exams e ON e.id = es.exam_id
                    JOIN recruitment_applications a ON a.exam_submission_id = es.id
                    JOIN job_positions p ON p.id = a.position_id
                    WHERE es.tenant_id=:tenant_id AND es.status='pending_review'
                    ORDER BY es.submitted_at DESC
                """),
                {"tenant_id": current_user["tenant_id"]},
            )
        ).mappings().all()
    items = []
    for row in rows:
        submission_id = str(row["id"])
        snapshot = await _graph.aget_state({
            "configurable": {"thread_id": await _get_thread_id(submission_id, current_user["tenant_id"])}
        })
        values = snapshot.values if snapshot else {}
        if not values:
            values = await _load_persisted_review(submission_id, current_user["tenant_id"]) or {}
        items.append({
            "submission_id": submission_id,
            "candidate_name": row["candidate_name"],
            "student_name": row["candidate_name"],
            "exam_title": row["exam_title"],
            "position_title": row["position_title"],
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
            "pre_review": values.get("pre_review_summary", {}),
            "weak_points": values.get("weak_points", [])[:3],
        })
    return {"items": items, "total": len(items)}


@router.get("/submissions/{submission_id}/review")
async def get_submission_review(submission_id: uuid.UUID, current_user: dict = Depends(get_current_user)):
    _require_recruiter(current_user)
    sid = str(submission_id)
    config = {"configurable": {"thread_id": await _get_thread_id(sid, current_user["tenant_id"])}}
    snapshot = await _graph.aget_state(config)
    if not snapshot or not snapshot.values:
        persisted = await _load_persisted_review(sid, current_user["tenant_id"])
        if persisted:
            return persisted
        raise HTTPException(status_code=404, detail="批改尚未完成或记录不存在")
    values = snapshot.values
    return {
        "submission_id": sid, "student_id": values.get("student_id", ""),
        "pre_review_summary": values.get("pre_review_summary", {}),
        "weak_points": values.get("weak_points", []),
        "weak_points_summary": values.get("weak_points_summary", ""),
    }


class ConfirmRequest(BaseModel):
    action: str
    modifications: list[dict] = Field(default_factory=list)


@router.post("/submissions/{submission_id}/confirm")
async def confirm_review(
    submission_id: uuid.UUID,
    req: ConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_recruiter(current_user)
    if req.action not in {"approve", "modify"}:
        raise HTTPException(status_code=400, detail="action 只能为 approve 或 modify")
    sid = str(submission_id)
    config = {"configurable": {"thread_id": await _get_thread_id(sid, current_user["tenant_id"])}}
    decision = {"action": req.action, "modifications": req.modifications,
                "teacher_id": current_user["user_id"]}
    snapshot = await _graph.aget_state(config)
    if not snapshot or not snapshot.values:
        persisted = await _load_persisted_review(sid, current_user["tenant_id"])
        if not persisted:
            raise HTTPException(status_code=404, detail="批改结果未持久化，请候选人重新提交笔试")
        modifications = {str(item.get("question_id")): item for item in req.modifications}
        async with AsyncSessionLocal() as session:
            async with session.begin():
                app = (await session.execute(text("""
                    SELECT a.id, a.candidate_id FROM recruitment_applications a
                    JOIN exam_submissions es ON es.id=a.exam_submission_id
                    WHERE es.id=:id AND a.tenant_id=:tenant_id
                    FOR UPDATE
                """), {"id": sid, "tenant_id": current_user["tenant_id"]})).mappings().fetchone()
                if not app:
                    raise HTTPException(status_code=404, detail="笔试申请不存在")
                for item in persisted["pre_review_summary"]["by_question"]:
                    qid = str(item["question_id"])
                    mod = modifications.get(qid, {})
                    final_score = mod.get("new_score", item.get("score", 0))
                    await session.execute(text("""
                        UPDATE exam_reviews SET teacher_score=:teacher_score,
                            teacher_comment=:comment, final_score=:final_score,
                            reviewed_by=:reviewed_by, reviewed_at=NOW(), needs_review=FALSE,
                            updated_at=NOW()
                        WHERE submission_id=:submission_id AND question_id=:question_id
                    """), {"teacher_score": mod.get("new_score"), "comment": mod.get("comment"),
                           "final_score": final_score, "reviewed_by": current_user["user_id"],
                           "submission_id": sid, "question_id": qid})
                await session.execute(text("""
                    UPDATE exam_submissions SET status='published', published_at=NOW(), updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND status='pending_review'
                """), {"id": sid, "tenant_id": current_user["tenant_id"]})
                await ApplicationService(session).record_exam_result(
                    application_id=str(app["id"]), tenant_id=current_user["tenant_id"],
                    submission_id=sid, needs_review=False,
                )
                totals = (await session.execute(text("""
                    SELECT COALESCE(SUM(er.final_score),0) AS final_score,
                           COALESCE(SUM(q.score),0) AS full_score
                    FROM exam_reviews er JOIN questions q ON q.id=er.question_id
                    WHERE er.submission_id=:id
                """), {"id": sid})).mappings().one()
        return {"submission_id": sid, "status": "published",
                "final_score": totals["final_score"], "full_score": totals["full_score"],
                "score_rate": (float(totals["final_score"]) / float(totals["full_score"]) if totals["full_score"] else 0),
                "weak_points": persisted["weak_points"],
                "weak_points_summary": persisted["weak_points_summary"]}
    try:
        result = await _graph.ainvoke(Command(resume=decision), config=config)
    except Exception as exc:
        logger.error("exam.confirm_failed", submission_id=sid, error=str(exc))
        raise HTTPException(status_code=500, detail="笔试结果发布失败") from exc
    structured = result.get("structured_output", {}) or {}
    return {
        "submission_id": sid, "status": "published",
        "final_score": structured.get("final_score", 0),
        "full_score": structured.get("full_score", 0),
        "score_rate": structured.get("score_rate", 0),
        "weak_points": structured.get("weak_points", []),
        "weak_points_summary": structured.get("weak_points_summary", ""),
    }
