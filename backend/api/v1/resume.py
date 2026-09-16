import asyncio
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import text

from backend.agents.resume.graph import build_resume_graph
from backend.core.application_service import ApplicationService
from backend.core.exceptions import (
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationStateError,
)
from backend.core.logger import get_logger
from backend.config import get_settings
from backend.core.object_storage import get_object_storage
from backend.dependencies import AsyncSessionLocal, get_current_user

router = APIRouter()
logger = get_logger(__name__)
_graph_local = threading.local()
_background_tasks: set[asyncio.Task] = set()
RESUME_REVIEW_TIMEOUT_SECONDS = 15 * 60


def _get_graph():
    if not hasattr(_graph_local, "graph"):
        _graph_local.graph = build_resume_graph()
    return _graph_local.graph


def _raise_application_http_error(exc: Exception) -> None:
    if isinstance(exc, ApplicationNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ApplicationPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ApplicationStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _candidate_review_payload(review_id: str, status: str, error_msg: str | None = None) -> dict:
    """候选人响应中禁止出现分数、报告、结论和排名。"""
    if status == "processing":
        return {"review_id": review_id, "status": "processing", "message": "简历正在处理中"}
    if status == "failed":
        return {
            "review_id": review_id,
            "status": "submitted",
            "message": "简历已提交，智能分析失败，已转人工处理",
            "retry_allowed": False,
        }
    return {"review_id": review_id, "status": "submitted", "message": "简历已提交，请等待 HR 通知"}


async def _mark_review_failed(
    review_id: str, error_msg: str, *, preserve_submission: bool = True
) -> None:
    """标记分析失败；仅在原始文件保存失败时回滚本次上传。"""
    async with AsyncSessionLocal() as session:
        application = (
            await session.execute(
                text("""
                    SELECT a.id AS application_id, a.candidate_id, a.tenant_id,
                           r.pdf_minio_path
                    FROM recruitment_applications a
                    JOIN resume_reviews r ON r.id = a.resume_review_id
                    WHERE a.resume_review_id = :review_id
                """),
                {"review_id": review_id},
            )
        ).mappings().fetchone()
        result = await session.execute(
            text("""
                UPDATE resume_reviews
                SET status = 'failed', error_msg = :error_msg, updated_at = NOW()
                WHERE id = :review_id AND status = 'processing'
            """),
            {"review_id": review_id, "error_msg": error_msg[:1000]},
        )
        if result.rowcount and application and not preserve_submission:
            await ApplicationService(session).reset_resume_after_failure(
                application_id=str(application["application_id"]),
                candidate_id=str(application["candidate_id"]),
                tenant_id=application["tenant_id"],
                review_id=review_id,
            )
        await session.commit()
    if application and not preserve_submission and get_settings().object_storage_enabled:
        try:
            await get_object_storage().delete(application["pdf_minio_path"])
        except Exception as exc:
            logger.warning("resume.object_cleanup_failed", review_id=review_id, error=str(exc))


@router.post("/upload", status_code=202)
async def upload_resume(
    application_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """候选人向自己的招聘申请提交 PDF 简历。"""
    if current_user.get("role") != "student":
        raise HTTPException(status_code=403, detail="仅候选人可以提交简历")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 格式")

    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大支持 20MB")
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件为空")

    application_id = str(application_id)
    review_id = str(uuid.uuid4())
    candidate_id = current_user["user_id"]
    tenant_id = current_user["tenant_id"]
    tmp_path = os.path.join(tempfile.gettempdir(), f"{review_id}_upload.pdf")
    object_key = f"resumes/{candidate_id}/{review_id}.pdf"

    try:
        async with AsyncSessionLocal() as session:
            service = ApplicationService(session)
            context = await service.get_resume_position_context(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                for_update=True,
            )
            await session.execute(
                text("""
                    INSERT INTO resume_reviews
                        (id, tenant_id, student_id, pdf_minio_path, status)
                    VALUES (:id, :tenant_id, :student_id, :path, 'processing')
                """),
                {
                    "id": review_id,
                    "tenant_id": tenant_id,
                    "student_id": candidate_id,
                    "path": object_key,
                },
            )
            await service.attach_resume_review(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                review_id=review_id,
            )
            await session.commit()
    except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
        _raise_application_http_error(exc)

    try:
        with open(tmp_path, "wb") as handle:
            handle.write(file_bytes)
    except Exception:
        await _mark_review_failed(
            review_id, "简历临时文件保存失败，请重试", preserve_submission=False
        )
        raise HTTPException(status_code=500, detail="简历保存失败，请重试")

    initial_state = {
        "messages": [], "student_id": candidate_id, "tenant_id": tenant_id,
        "review_id": review_id, "application_id": context.application_id,
        "position_id": context.position_id, "position_title": context.position_title,
        "job_description": context.job_description,
        "job_requirements": context.job_requirements,
        "resume_pass_score": context.resume_pass_score,
        "pdf_minio_path": object_key, "pdf_local_path": tmp_path,
        "raw_text": "", "page_count": 0, "structured": None,
        "dimension_scores": [], "weighted_score": 0.0, "issues": [],
        "summary": None, "fallback_used": False, "structured_output": None,
    }

    def _on_task_done(task: asyncio.Task) -> None:
        _background_tasks.discard(task)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        failure = None
        if task.cancelled():
            failure = "简历任务被服务重启中断，请重试"
        else:
            exc = task.exception()
            if exc:
                failure = f"简历任务执行失败：{exc}"
                logger.error("resume.background_task_failed", review_id=review_id, error=str(exc))
        if failure:
            try:
                asyncio.get_running_loop().create_task(_mark_review_failed(review_id, failure))
            except RuntimeError:
                pass

    task = asyncio.create_task(_get_graph().ainvoke(initial_state))
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return _candidate_review_payload(review_id, "processing")


@router.get("/reviews/{review_id}")
async def get_review(review_id: str, current_user: dict = Depends(get_current_user)):
    """候选人只看提交状态；招聘人员可查看租户内完整评审报告。"""
    recruiter = current_user.get("role") in {"teacher", "admin", "hr", "recruiter"}
    ownership = "" if recruiter else " AND r.student_id = :user_id"
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text(f"""
                    SELECT r.id, r.status, r.scores, r.issues, r.summary, r.error_msg,
                           r.created_at, r.updated_at
                    FROM resume_reviews r
                    WHERE r.id = :review_id AND r.tenant_id = :tenant_id {ownership}
                """),
                {
                    "review_id": review_id,
                    "tenant_id": current_user["tenant_id"],
                    "user_id": current_user["user_id"],
                },
            )
        ).mappings().fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="简历记录不存在")

    if row["status"] == "processing":
        last_ts = row["updated_at"] or row["created_at"]
        if isinstance(last_ts, datetime):
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if elapsed >= RESUME_REVIEW_TIMEOUT_SECONDS:
                await _mark_review_failed(review_id, "简历任务超时或被中断，请重新提交")
                row = dict(row)
                row["status"] = "failed"
                row["error_msg"] = "简历任务超时或被中断，请重新提交"

    if not recruiter:
        return _candidate_review_payload(review_id, row["status"], row["error_msg"])
    scores = row["scores"] or {}
    if isinstance(scores, str):
        import json
        scores = json.loads(scores)
    return {
        "review_id": review_id,
        "status": row["status"],
        "weighted_score": scores.get("weighted_score", 0),
        "dimension_scores": scores.get("dimension_scores", []),
        "issues": row["issues"],
        "summary": row["summary"],
        "error_msg": row["error_msg"],
    }


@router.get("/reviews")
async def list_reviews(current_user: dict = Depends(get_current_user)):
    """候选人的提交记录列表，不包含任何评估字段。"""
    if current_user.get("role") != "student":
        raise HTTPException(status_code=403, detail="招聘人员请从候选人看板查看简历报告")
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT id, status, created_at
                    FROM resume_reviews
                    WHERE student_id = :student_id AND tenant_id = :tenant_id
                    ORDER BY created_at DESC LIMIT 50
                """),
                {"student_id": current_user["user_id"], "tenant_id": current_user["tenant_id"]},
            )
        ).mappings().all()
    items = [
        {
            "review_id": str(row["id"]),
            "status": "failed" if row["status"] == "failed" else (
                "processing" if row["status"] == "processing" else "submitted"
            ),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


@router.delete("/reviews/{review_id}")
async def delete_review(review_id: str, current_user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=403, detail="招聘申请中的简历记录不允许删除")
