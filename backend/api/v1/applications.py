"""候选人岗位与招聘申请 API，不返回任何评估结果。"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.core.application_service import ApplicationService, CandidateTaskView
from backend.core.exceptions import (
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationStateError,
)
from backend.dependencies import AsyncSessionLocal, get_current_user

router = APIRouter()


class PositionItem(BaseModel):
    position_id: str
    code: str
    title: str
    department: str | None = None
    jd_text: str
    requirements: dict[str, Any] = Field(default_factory=dict)
    apply_deadline: datetime | None = None
    application_id: str | None = None


class PositionListResponse(BaseModel):
    items: list[PositionItem]


class CreateApplicationRequest(BaseModel):
    position_id: uuid.UUID


class ApplicationListResponse(BaseModel):
    items: list[CandidateTaskView]


def _require_candidate(current_user: dict) -> None:
    if current_user.get("role") != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有候选人可以使用申请任务中心",
        )


def _raise_application_http_error(exc: Exception) -> None:
    if isinstance(exc, ApplicationNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ApplicationPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ApplicationStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/positions", response_model=PositionListResponse)
async def list_open_positions(current_user: dict = Depends(get_current_user)):
    """列出当前租户仍可申请的岗位，并标记本人已有的申请。"""
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("""
                SELECT p.id, p.code, p.title, p.department, p.jd_text,
                       p.requirements, p.apply_deadline,
                       a.id AS application_id
                FROM job_positions AS p
                LEFT JOIN recruitment_applications AS a
                  ON a.position_id = p.id
                 AND a.candidate_id = :candidate_id
                 AND a.tenant_id = p.tenant_id
                WHERE p.tenant_id = :tenant_id
                  AND p.status = 'open'
                  AND (p.apply_deadline IS NULL OR NOW() <= p.apply_deadline)
                ORDER BY p.created_at DESC
            """),
            {"candidate_id": current_user["user_id"],
             "tenant_id": current_user["tenant_id"]},
        )).mappings().all()

    return PositionListResponse(items=[PositionItem(
        position_id=str(row["id"]),
        code=row["code"],
        title=row["title"],
        department=row["department"],
        jd_text=row["jd_text"],
        requirements=row["requirements"] or {},
        apply_deadline=row["apply_deadline"],
        application_id=(str(row["application_id"]) if row["application_id"] else None),
    ) for row in rows])


@router.post("/applications", response_model=CandidateTaskView, status_code=201)
async def create_application(
    req: CreateApplicationRequest,
    current_user: dict = Depends(get_current_user),
):
    """为开放岗位创建申请，响应只包含候选人可见任务。"""
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        try:
            async with session.begin():
                snapshot = await ApplicationService(session).create_application(
                    candidate_id=current_user["user_id"],
                    position_id=str(req.position_id),
                    tenant_id=current_user["tenant_id"],
                )
                view = await ApplicationService(session).get_candidate_tasks(
                    application_id=snapshot.id,
                    candidate_id=current_user["user_id"],
                    tenant_id=current_user["tenant_id"],
                )
            return view
        except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
            _raise_application_http_error(exc)


@router.get("/applications", response_model=ApplicationListResponse)
async def list_my_applications(current_user: dict = Depends(get_current_user)):
    """返回本人的申请任务列表，不返回内部申请状态或筛选结果。"""
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        ids = (await session.execute(
            text("""
                SELECT id
                FROM recruitment_applications
                WHERE candidate_id = :candidate_id AND tenant_id = :tenant_id
                ORDER BY created_at DESC
            """),
            {"candidate_id": current_user["user_id"],
             "tenant_id": current_user["tenant_id"]},
        )).scalars().all()
        service = ApplicationService(session)
        items = [await service.get_candidate_tasks(
            application_id=str(application_id),
            candidate_id=current_user["user_id"],
            tenant_id=current_user["tenant_id"],
        ) for application_id in ids]
    return ApplicationListResponse(items=items)


@router.get("/applications/{application_id}/tasks", response_model=CandidateTaskView)
async def get_application_tasks(
    application_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
):
    """读取单个申请的安全任务视图。"""
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        try:
            return await ApplicationService(session).get_candidate_tasks(
                application_id=str(application_id),
                candidate_id=current_user["user_id"],
                tenant_id=current_user["tenant_id"],
            )
        except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
            _raise_application_http_error(exc)
