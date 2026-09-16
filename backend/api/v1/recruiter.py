"""招聘方候选人看板与最终决策 API。

所有接口都按租户和招聘方角色隔离；本模块的响应禁止用于候选人端。
"""

import json
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query,
    UploadFile, status,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.dependencies import AsyncSessionLocal, get_current_user
from backend.core.application_service import ApplicationService, ResumeDecision
from backend.core.exceptions import ApplicationStateError
from backend.core.knowledge_ingestion import ingest_knowledge_document

router = APIRouter(prefix="/recruiter")
RECRUITER_ROLES = {"teacher", "admin", "hr", "recruiter"}
DECISIONS = {"pending", "advance", "rejected", "hired", "withdrawn"}
POSITION_STATUSES = {"draft", "open", "closed"}
DOCUMENT_TYPES = {"position_jd", "policy", "company", "faq", "question_bank"}
MAX_KNOWLEDGE_FILE_SIZE = 20 * 1024 * 1024


class RecruiterApplicationItem(BaseModel):
    application_id: str
    candidate_id: str
    candidate_name: str | None = None
    position_id: str
    position_title: str
    application_status: str
    resume_decision: str
    exam_status: str
    interview_status: str
    final_decision: str | None = None
    resume_score: float | None = None
    exam_score: float | None = None
    exam_full_score: float | None = None
    interview_score: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RecruiterApplicationListResponse(BaseModel):
    items: list[RecruiterApplicationItem]
    total: int
    page: int
    page_size: int


class FinalDecisionRequest(BaseModel):
    decision: str = Field(..., description="pending/advance/rejected/hired/withdrawn")
    comment: str | None = Field(None, max_length=2000)


class FinalDecisionResponse(BaseModel):
    application_id: str
    final_decision: str
    decided_by: str
    decided_at: datetime
    comment: str | None = None


class ResumeDecisionRequest(BaseModel):
    decision: str = Field(..., description="passed/rejected")
    comment: str | None = Field(None, max_length=2000)


class RecruiterPositionItem(BaseModel):
    position_id: str
    code: str
    title: str
    department: str | None = None
    jd_text: str
    requirements: dict[str, Any] = Field(default_factory=dict)
    evaluation_weights: dict[str, float] = Field(default_factory=dict)
    resume_pass_score: float
    exam_window_hours: int
    interview_window_hours: int
    exam_id: str | None = None
    exam_title: str | None = None
    status: str
    apply_deadline: datetime | None = None
    application_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PositionCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=128)
    department: str | None = Field(None, max_length=128)
    jd_text: str = Field(..., min_length=1)
    requirements: dict[str, Any] = Field(default_factory=dict)
    evaluation_weights: dict[str, float] = Field(
        default_factory=lambda: {"resume": 0.3, "exam": 0.3, "interview": 0.4}
    )
    resume_pass_score: float = Field(70, ge=0, le=100)
    exam_window_hours: int = Field(72, gt=0)
    interview_window_hours: int = Field(72, gt=0)
    exam_id: uuid.UUID | None = None
    status: str = "draft"
    apply_deadline: datetime | None = None


class PositionUpdateRequest(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=64)
    title: str | None = Field(None, min_length=1, max_length=128)
    department: str | None = Field(None, max_length=128)
    jd_text: str | None = Field(None, min_length=1)
    requirements: dict[str, Any] | None = None
    evaluation_weights: dict[str, float] | None = None
    resume_pass_score: float | None = Field(None, ge=0, le=100)
    exam_window_hours: int | None = Field(None, gt=0)
    interview_window_hours: int | None = Field(None, gt=0)
    exam_id: uuid.UUID | None = None
    status: str | None = None
    apply_deadline: datetime | None = None


class KnowledgeDocumentItem(BaseModel):
    document_id: str
    filename: str
    document_type: str
    position_id: str | None = None
    position_title: str | None = None
    status: str
    use_context: bool
    chunk_count: int | None = None
    error_msg: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime


def _require_recruiter(current_user: dict) -> None:
    if current_user.get("role") not in RECRUITER_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有招聘方可以查看候选人结果")


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value


def _validate_position_values(payload: PositionCreateRequest | PositionUpdateRequest) -> None:
    if payload.status is not None and payload.status not in POSITION_STATUSES:
        raise HTTPException(status_code=400, detail="岗位状态必须是 draft、open 或 closed")
    if payload.evaluation_weights is not None:
        weights = payload.evaluation_weights
        if set(weights) != {"resume", "exam", "interview"}:
            raise HTTPException(status_code=400, detail="评估权重必须包含 resume、exam、interview")
        if any(value < 0 for value in weights.values()) or abs(sum(weights.values()) - 1) > 0.001:
            raise HTTPException(status_code=400, detail="评估权重必须非负且总和为 1")


def _position_item(row: Any) -> RecruiterPositionItem:
    return RecruiterPositionItem(
        position_id=str(row["id"]),
        code=row["code"],
        title=row["title"],
        department=row.get("department"),
        jd_text=row["jd_text"],
        requirements=_json(row.get("requirements"), {}),
        evaluation_weights=_json(row.get("evaluation_weights"), {}),
        resume_pass_score=float(row["resume_pass_score"]),
        exam_window_hours=row["exam_window_hours"],
        interview_window_hours=row["interview_window_hours"],
        exam_id=str(row["exam_id"]) if row.get("exam_id") else None,
        exam_title=row.get("exam_title"),
        status=row["status"],
        apply_deadline=row.get("apply_deadline"),
        application_count=row.get("application_count", 0),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


_POSITION_SELECT = """
    SELECT p.id, p.code, p.title, p.department, p.jd_text, p.requirements,
           p.evaluation_weights, p.resume_pass_score, p.exam_window_hours,
           p.interview_window_hours, p.exam_id, e.title AS exam_title,
           p.status, p.apply_deadline, p.created_at, p.updated_at,
           (SELECT COUNT(*) FROM recruitment_applications a
            WHERE a.position_id = p.id AND a.tenant_id = p.tenant_id) AS application_count
    FROM job_positions p
    LEFT JOIN exams e ON e.id = p.exam_id AND e.tenant_id = p.tenant_id
    WHERE p.tenant_id = :tenant_id
"""


async def _get_position_row(session: Any, position_id: str, tenant_id: str) -> Any:
    row = (await session.execute(
        text(_POSITION_SELECT + " AND p.id = :position_id"),
        {"tenant_id": tenant_id, "position_id": position_id},
    )).mappings().fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="岗位不存在")
    return row


async def _require_available_exam(
    session: Any,
    exam_id: str,
    tenant_id: str,
    position_id: str | None = None,
) -> None:
    row = (await session.execute(text("""
        SELECT e.id, p.id AS assigned_position_id
        FROM exams e
        LEFT JOIN job_positions p ON p.exam_id = e.id
        WHERE e.id = :exam_id AND e.tenant_id = :tenant_id AND e.is_active = TRUE
    """), {"exam_id": exam_id, "tenant_id": tenant_id})).mappings().fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="所选试卷不存在或已停用")
    assigned = row.get("assigned_position_id")
    if assigned and str(assigned) != position_id:
        raise HTTPException(status_code=409, detail="所选试卷已绑定其他岗位")


@router.get("/positions")
async def list_recruiter_positions(current_user: dict = Depends(get_current_user)):
    """列出当前租户的全部岗位，包括草稿和已关闭岗位。"""
    _require_recruiter(current_user)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text(_POSITION_SELECT + " ORDER BY p.created_at DESC"),
            {"tenant_id": current_user["tenant_id"]},
        )).mappings().all()
    return {"items": [_position_item(row) for row in rows]}


@router.get("/exams")
async def list_recruiter_exams(current_user: dict = Depends(get_current_user)):
    """列出岗位可绑定的试卷，并标记已经占用的试卷。"""
    _require_recruiter(current_user)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT e.id, e.title, e.description, e.is_active,
                   p.id AS assigned_position_id, p.title AS assigned_position_title
            FROM exams e
            LEFT JOIN job_positions p ON p.exam_id = e.id AND p.tenant_id = e.tenant_id
            WHERE e.tenant_id = :tenant_id AND e.is_active = TRUE
            ORDER BY e.created_at DESC
        """), {"tenant_id": current_user["tenant_id"]})).mappings().all()
    return {"items": [{
        "exam_id": str(row["id"]),
        "title": row["title"],
        "description": row.get("description"),
        "assigned_position_id": (
            str(row["assigned_position_id"]) if row.get("assigned_position_id") else None
        ),
        "assigned_position_title": row.get("assigned_position_title"),
    } for row in rows]}


@router.post("/positions", response_model=RecruiterPositionItem, status_code=201)
async def create_recruiter_position(
    req: PositionCreateRequest,
    current_user: dict = Depends(get_current_user),
):
    """创建岗位；开放岗位必须绑定一份未被其他岗位占用的试卷。"""
    _require_recruiter(current_user)
    _validate_position_values(req)
    exam_id = str(req.exam_id) if req.exam_id else None
    if req.status == "open" and not exam_id:
        raise HTTPException(status_code=400, detail="开放岗位前必须绑定试卷，可先保存为草稿")
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                if exam_id:
                    await _require_available_exam(session, exam_id, current_user["tenant_id"])
                position_id = str(uuid.uuid4())
                await session.execute(text("""
                    INSERT INTO job_positions (
                        id, tenant_id, code, title, department, jd_text,
                        requirements, evaluation_weights, resume_pass_score,
                        exam_window_hours, interview_window_hours, exam_id,
                        status, apply_deadline, created_by
                    ) VALUES (
                        :id, :tenant_id, :code, :title, :department, :jd_text,
                        CAST(:requirements AS JSONB), CAST(:evaluation_weights AS JSONB),
                        :resume_pass_score, :exam_window_hours, :interview_window_hours,
                        :exam_id, :status, :apply_deadline, :created_by
                    )
                """), {
                    "id": position_id,
                    "tenant_id": current_user["tenant_id"],
                    "code": req.code.strip(),
                    "title": req.title.strip(),
                    "department": req.department.strip() if req.department else None,
                    "jd_text": req.jd_text.strip(),
                    "requirements": json.dumps(req.requirements, ensure_ascii=False),
                    "evaluation_weights": json.dumps(req.evaluation_weights),
                    "resume_pass_score": req.resume_pass_score,
                    "exam_window_hours": req.exam_window_hours,
                    "interview_window_hours": req.interview_window_hours,
                    "exam_id": exam_id,
                    "status": req.status,
                    "apply_deadline": req.apply_deadline,
                    "created_by": current_user["user_id"],
                })
            row = await _get_position_row(
                session, position_id, current_user["tenant_id"]
            )
        return _position_item(row)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="岗位编号或试卷已被使用") from exc


@router.patch("/positions/{position_id}", response_model=RecruiterPositionItem)
async def update_recruiter_position(
    position_id: uuid.UUID,
    req: PositionUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """编辑岗位配置或切换草稿、开放、关闭状态。"""
    _require_recruiter(current_user)
    _validate_position_values(req)
    pid = str(position_id)
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                current = (await session.execute(text("""
                    SELECT id, exam_id, status FROM job_positions
                    WHERE id = :position_id AND tenant_id = :tenant_id
                    FOR UPDATE
                """), {
                    "position_id": pid,
                    "tenant_id": current_user["tenant_id"],
                })).mappings().fetchone()
                if not current:
                    raise HTTPException(status_code=404, detail="岗位不存在")

                provided = req.model_fields_set
                final_exam_id = (
                    str(req.exam_id) if req.exam_id else None
                ) if "exam_id" in provided else (
                    str(current["exam_id"]) if current.get("exam_id") else None
                )
                final_status = req.status if "status" in provided else current["status"]
                if final_status == "open" and not final_exam_id:
                    raise HTTPException(status_code=400, detail="开放岗位前必须绑定试卷")
                if final_exam_id and (
                    "exam_id" in provided or final_status == "open"
                ):
                    await _require_available_exam(
                        session, final_exam_id, current_user["tenant_id"], pid
                    )

                assignments: list[str] = []
                params: dict[str, Any] = {
                    "position_id": pid,
                    "tenant_id": current_user["tenant_id"],
                }
                scalar_fields = {
                    "code", "title", "department", "jd_text", "resume_pass_score",
                    "exam_window_hours", "interview_window_hours", "status", "apply_deadline",
                }
                for field_name in scalar_fields & provided:
                    value = getattr(req, field_name)
                    if isinstance(value, str):
                        value = value.strip()
                    assignments.append(f"{field_name} = :{field_name}")
                    params[field_name] = value
                if "exam_id" in provided:
                    assignments.append("exam_id = :exam_id")
                    params["exam_id"] = final_exam_id
                for field_name in {"requirements", "evaluation_weights"} & provided:
                    assignments.append(f"{field_name} = CAST(:{field_name} AS JSONB)")
                    params[field_name] = json.dumps(getattr(req, field_name), ensure_ascii=False)
                if assignments:
                    await session.execute(text(
                        "UPDATE job_positions SET " + ", ".join(assignments) +
                        ", updated_at = NOW() WHERE id = :position_id AND tenant_id = :tenant_id"
                    ), params)
            row = await _get_position_row(session, pid, current_user["tenant_id"])
        return _position_item(row)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="岗位编号或试卷已被使用") from exc


def _knowledge_item(row: Any) -> KnowledgeDocumentItem:
    return KnowledgeDocumentItem(
        document_id=str(row["id"]),
        filename=row["filename"],
        document_type=row["document_type"],
        position_id=str(row["position_id"]) if row.get("position_id") else None,
        position_title=row.get("position_title"),
        status=row["status"],
        use_context=row["use_context"],
        chunk_count=row.get("chunk_count"),
        error_msg=row.get("error_msg"),
        created_by_name=row.get("created_by_name"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_KNOWLEDGE_SELECT = """
    SELECT d.id, d.filename, d.document_type, d.position_id,
           p.title AS position_title, d.status, d.use_context,
           d.chunk_count, d.error_msg, u.username AS created_by_name,
           d.created_at, d.updated_at
    FROM knowledge_documents d
    LEFT JOIN job_positions p ON p.id = d.position_id AND p.tenant_id = d.tenant_id
    LEFT JOIN users u ON u.id = d.created_by
    WHERE d.tenant_id = :tenant_id
"""


@router.get("/knowledge-documents")
async def list_knowledge_documents(current_user: dict = Depends(get_current_user)):
    """查看当前租户上传过的招聘知识库文档及处理状态。"""
    _require_recruiter(current_user)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text(_KNOWLEDGE_SELECT + " ORDER BY d.created_at DESC"),
            {"tenant_id": current_user["tenant_id"]},
        )).mappings().all()
    return {"items": [_knowledge_item(row) for row in rows]}


@router.post("/knowledge-documents", status_code=202)
async def upload_knowledge_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    position_id: str | None = Form(None),
    use_context: bool = Form(False),
    current_user: dict = Depends(get_current_user),
):
    """接收文档后立即返回，切分、向量化和 Milvus 写入在后台完成。"""
    _require_recruiter(current_user)
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail="不支持的知识库文档类型")
    position_id = position_id.strip() if position_id else None
    if document_type == "position_jd" and not position_id:
        raise HTTPException(status_code=400, detail="岗位 JD 文档必须选择所属岗位")
    if position_id:
        try:
            position_id = str(uuid.UUID(position_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="岗位 ID 格式错误") from exc
        async with AsyncSessionLocal() as session:
            await _get_position_row(session, position_id, current_user["tenant_id"])

    filename = os.path.basename((file.filename or "").replace("\\", "/"))
    extension = os.path.splitext(filename)[1].lower()
    if extension not in {".pdf", ".md", ".markdown"}:
        raise HTTPException(status_code=400, detail="仅支持 PDF、Markdown 文档")
    content = await file.read(MAX_KNOWLEDGE_FILE_SIZE + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_KNOWLEDGE_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件过大，最大支持 20MB")

    document_id = str(uuid.uuid4())
    file_path = os.path.join(tempfile.gettempdir(), f"pinda-kb-{document_id}{extension}")
    with open(file_path, "wb") as output:
        output.write(content)

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                INSERT INTO knowledge_documents (
                    id, tenant_id, position_id, document_type, filename,
                    status, use_context, created_by
                ) VALUES (
                    :id, :tenant_id, :position_id, :document_type, :filename,
                    'processing', :use_context, :created_by
                )
            """), {
                "id": document_id,
                "tenant_id": current_user["tenant_id"],
                "position_id": position_id,
                "document_type": document_type,
                "filename": filename,
                "use_context": use_context,
                "created_by": current_user["user_id"],
            })
            await session.commit()
    except Exception:
        try:
            os.remove(file_path)
        except FileNotFoundError:
            pass
        raise

    background_tasks.add_task(
        ingest_knowledge_document,
        document_id=document_id,
        file_path=file_path,
        tenant_id=current_user["tenant_id"],
        document_type=document_type,
        position_id=position_id,
        use_context=use_context,
    )
    return {
        "document_id": document_id,
        "status": "processing",
        "message": "文档已接收，正在后台构建知识库",
    }


def _item(row: Any) -> RecruiterApplicationItem:
    return RecruiterApplicationItem(
        application_id=str(row["application_id"]),
        candidate_id=str(row["candidate_id"]),
        candidate_name=row.get("candidate_name"),
        position_id=str(row["position_id"]),
        position_title=row["position_title"],
        application_status=row["application_status"],
        resume_decision=row["resume_decision"],
        exam_status=row["exam_status"],
        interview_status=row["interview_status"],
        final_decision=row.get("final_decision"),
        resume_score=(float(row["resume_score"]) if row.get("resume_score") is not None else None),
        exam_score=(float(row["exam_score"]) if row.get("exam_score") is not None else None),
        exam_full_score=(float(row["exam_full_score"]) if row.get("exam_full_score") is not None else None),
        interview_score=(float(row["interview_score"]) if row.get("interview_score") is not None else None),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


_AGGREGATE_SELECT = """
    SELECT a.id AS application_id, a.candidate_id, u.username AS candidate_name,
           a.position_id, p.title AS position_title,
           a.status AS application_status, a.resume_decision,
           a.exam_status, a.interview_status, a.final_decision,
           a.resume_review_id, a.exam_submission_id, a.interview_session_id,
           a.created_at, a.updated_at,
           CASE WHEN r.status = 'done'
                THEN NULLIF((r.scores->>'weighted_score')::numeric, 0)
                ELSE NULL END AS resume_score,
           CASE WHEN a.exam_submission_id IS NOT NULL
                THEN (SELECT COALESCE(SUM(COALESCE(er.final_score, er.teacher_score, er.ai_score)), 0)
                      FROM exam_reviews er WHERE er.submission_id = a.exam_submission_id)
                ELSE NULL END AS exam_score,
           CASE WHEN a.exam_submission_id IS NOT NULL
                THEN (SELECT COALESCE(SUM(q.score), 0)
                      FROM questions q
                      JOIN exam_reviews er ON er.question_id = q.id
                      WHERE er.submission_id = a.exam_submission_id)
                ELSE NULL END AS exam_full_score,
           i.overall_score AS interview_score
    FROM recruitment_applications a
    JOIN job_positions p ON p.id = a.position_id AND p.tenant_id = a.tenant_id
    LEFT JOIN users u ON u.id = a.candidate_id
    LEFT JOIN resume_reviews r ON r.id = a.resume_review_id AND r.tenant_id = a.tenant_id
    LEFT JOIN interview_sessions i ON i.id = a.interview_session_id AND i.tenant_id = a.tenant_id
    WHERE a.tenant_id = :tenant_id
"""


@router.get("/applications", response_model=RecruiterApplicationListResponse)
async def list_recruiter_applications(
    position_id: uuid.UUID | None = Query(None),
    application_status: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    _require_recruiter(current_user)
    clauses: list[str] = []
    params: dict[str, Any] = {"tenant_id": current_user["tenant_id"]}
    if position_id:
        clauses.append("a.position_id = :position_id")
        params["position_id"] = str(position_id)
    if application_status:
        if application_status not in {"draft", "resume_processing", "screening", "completed", "rejected", "withdrawn"}:
            raise HTTPException(status_code=400, detail="不支持的申请状态")
        clauses.append("a.status = :application_status")
        params["application_status"] = application_status
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    params.update({"limit": page_size, "offset": (page - 1) * page_size})
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text(_AGGREGATE_SELECT + where + " ORDER BY a.created_at DESC LIMIT :limit OFFSET :offset"),
            params,
        )).mappings().all()
        total = (await session.execute(
            text("SELECT COUNT(*) FROM recruitment_applications a WHERE a.tenant_id = :tenant_id" + where),
            params,
        )).scalar_one()
    return RecruiterApplicationListResponse(
        items=[_item(row) for row in rows], total=total, page=page, page_size=page_size,
    )


@router.get("/applications/{application_id}")
async def get_recruiter_application(
    application_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
):
    """聚合简历、笔试、面试及最终决策，供招聘方追溯。"""
    _require_recruiter(current_user)
    aid = str(application_id)
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text(_AGGREGATE_SELECT + " AND a.id = :application_id"),
            {"tenant_id": current_user["tenant_id"], "application_id": aid},
        )).mappings().fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="招聘申请不存在")

        resume = None
        if row.get("resume_review_id"):
            resume = (await session.execute(text("""
                SELECT id, status, structured_data, scores, issues, summary, error_msg
                FROM resume_reviews WHERE id = :id AND tenant_id = :tenant_id
            """), {"id": row["resume_review_id"], "tenant_id": current_user["tenant_id"]})).mappings().fetchone()

        exam = None
        if row.get("exam_submission_id"):
            exam_rows = (await session.execute(text("""
                SELECT id, status, submitted_at, published_at, weak_points, weak_points_summary
                FROM exam_submissions WHERE id = :id AND tenant_id = :tenant_id
            """), {"id": row["exam_submission_id"], "tenant_id": current_user["tenant_id"]})).mappings().fetchone()
            if exam_rows:
                details = (await session.execute(text("""
                    SELECT question_id, question_type, student_answer, ai_score, ai_feedback,
                           teacher_score, teacher_comment, final_score, needs_review,
                           reviewed_by, reviewed_at
                    FROM exam_reviews WHERE submission_id = :id
                    ORDER BY created_at
                """), {"id": row["exam_submission_id"]})).mappings().all()
                exam = {**dict(exam_rows), "reviews": [dict(item) for item in details]}

        interview = None
        if row.get("interview_session_id"):
            interview = (await session.execute(text("""
                SELECT session_id, status, target_position, overall_score, report, finished_at
                FROM interview_sessions WHERE id = :id AND tenant_id = :tenant_id
            """), {"id": row["interview_session_id"], "tenant_id": current_user["tenant_id"]})).mappings().fetchone()

        decision_history = (await session.execute(text("""
            SELECT actor_id, previous_decision, new_decision, comment, created_at
            FROM recruitment_decision_audits
            WHERE application_id = :application_id AND tenant_id = :tenant_id
            ORDER BY created_at DESC
        """), {
            "application_id": aid,
            "tenant_id": current_user["tenant_id"],
        })).mappings().all()

    return {
        "application": _item(row).model_dump(),
        "candidate": {"candidate_id": str(row["candidate_id"]), "name": row.get("candidate_name")},
        "position": {"position_id": str(row["position_id"]), "title": row["position_title"]},
        "resume": (dict(resume) if resume else None),
        "exam": exam,
        "interview": (dict(interview) if interview else None),
        "final_decision": row.get("final_decision"),
        "decision_history": [dict(item) for item in decision_history],
    }


@router.post("/applications/{application_id}/resume-decision")
async def decide_resume(
    application_id: uuid.UUID,
    req: ResumeDecisionRequest,
    current_user: dict = Depends(get_current_user),
):
    """招聘官复核低置信度简历；通过时原子解锁笔试和初面。"""
    _require_recruiter(current_user)
    if req.decision not in {ResumeDecision.PASSED.value, ResumeDecision.REJECTED.value}:
        raise HTTPException(status_code=400, detail="简历复核结论必须是 passed 或 rejected")
    aid = str(application_id)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = (await session.execute(text("""
                SELECT resume_review_id, resume_decision
                FROM recruitment_applications
                WHERE id=:application_id AND tenant_id=:tenant_id
                FOR UPDATE
            """), {"application_id": aid, "tenant_id": current_user["tenant_id"]})).mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="招聘申请不存在")
            if not row["resume_review_id"] or row["resume_decision"] != "manual_review":
                raise HTTPException(status_code=409, detail="当前申请不需要人工复核简历")
            try:
                snapshot = await ApplicationService(session).apply_resume_result(
                    application_id=aid,
                    tenant_id=current_user["tenant_id"],
                    review_id=str(row["resume_review_id"]),
                    decision=req.decision,
                )
            except ApplicationStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            await session.execute(text("""
                INSERT INTO recruitment_decision_audits
                    (tenant_id, application_id, actor_id, previous_decision, new_decision, comment)
                VALUES (:tenant_id, :application_id, :actor_id, :previous_decision, :new_decision, :comment)
            """), {
                "tenant_id": current_user["tenant_id"], "application_id": aid,
                "actor_id": current_user["user_id"], "previous_decision": "manual_review",
                "new_decision": req.decision, "comment": req.comment,
            })
    return {
        "application_id": snapshot.id,
        "resume_decision": snapshot.resume_decision,
        "exam_status": snapshot.exam_status,
        "interview_status": snapshot.interview_status,
        "message": "简历复核完成",
    }


@router.post("/applications/{application_id}/decision", response_model=FinalDecisionResponse)
async def decide_recruiter_application(
    application_id: uuid.UUID,
    req: FinalDecisionRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_recruiter(current_user)
    if req.decision not in DECISIONS:
        raise HTTPException(status_code=400, detail="不支持的招聘决策")
    aid = str(application_id)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            current = (await session.execute(text("""
                SELECT final_decision
                FROM recruitment_applications
                WHERE id = :application_id AND tenant_id = :tenant_id
                FOR UPDATE
            """), {
                "application_id": aid,
                "tenant_id": current_user["tenant_id"],
            })).mappings().fetchone()
            if not current:
                raise HTTPException(status_code=404, detail="招聘申请不存在")
            updated = (await session.execute(text("""
                UPDATE recruitment_applications
                SET final_decision = :decision,
                    decided_by = :decided_by,
                    decided_at = NOW(),
                    updated_at = NOW(),
                    status = CASE
                        WHEN :decision_check = 'rejected' THEN 'rejected'
                        WHEN :decision_check = 'withdrawn' THEN 'withdrawn'
                        WHEN :decision_check IN ('advance', 'hired') THEN 'completed'
                        ELSE status END
                WHERE id = :application_id AND tenant_id = :tenant_id
                RETURNING id, final_decision, decided_by, decided_at
            """), {
                "decision": req.decision, "decision_check": req.decision,
                "decided_by": current_user["user_id"],
                "application_id": aid, "tenant_id": current_user["tenant_id"],
            })).mappings().fetchone()
            await session.execute(text("""
                INSERT INTO recruitment_decision_audits (
                    tenant_id, application_id, actor_id, previous_decision,
                    new_decision, comment
                ) VALUES (
                    :tenant_id, :application_id, :actor_id, :previous_decision,
                    :new_decision, :comment
                )
            """), {
                "tenant_id": current_user["tenant_id"],
                "application_id": aid,
                "actor_id": current_user["user_id"],
                "previous_decision": current["final_decision"],
                "new_decision": req.decision,
                "comment": req.comment,
            })
            notification_title, notification_content = {
                "advance": ("招聘流程更新", "您的申请已进入下一阶段，请留意后续安排。"),
                "hired": ("录用通知", "恭喜您，本次招聘申请已通过，后续入职安排请等待 HR 联系。"),
                "rejected": ("招聘结果通知", "很遗憾，本次招聘申请未通过，感谢您的参与。"),
                "withdrawn": ("申请撤回通知", "本次招聘申请已撤回，如有疑问请联系招聘方。"),
                "pending": ("招聘流程更新", "招聘方正在继续处理您的申请，请耐心等待。"),
            }[req.decision]
            # 同一申请的同类决策只保留一条通知；重复提交时更新内容并恢复为未读。
            await session.execute(text("""
                INSERT INTO notifications (
                    tenant_id, recipient_id, application_id, channel,
                    reminder_type, title, content, idempotency_key
                ) VALUES (
                    :tenant_id, :recipient_id, :application_id, 'in_app',
                    :reminder_type, :title, :content, :idempotency_key
                ) ON CONFLICT (idempotency_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    sent_at = NOW(),
                    read_at = NULL
            """), {
                "tenant_id": current_user["tenant_id"],
                "recipient_id": (await session.execute(text(
                    "SELECT candidate_id FROM recruitment_applications WHERE id=:id AND tenant_id=:tenant"
                ), {"id": aid, "tenant": current_user["tenant_id"]})).scalar_one(),
                "application_id": aid,
                "reminder_type": "decision_" + req.decision,
                "title": notification_title,
                "content": notification_content,
                "idempotency_key": f"{aid}:decision:{req.decision}",
            })
    return FinalDecisionResponse(
        application_id=aid,
        final_decision=updated["final_decision"],
        decided_by=str(updated["decided_by"]),
        decided_at=updated["decided_at"],
        comment=req.comment,
    )
