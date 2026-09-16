"""候选人站内通知接口，只返回当前用户当前租户的数据。"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from backend.dependencies import AsyncSessionLocal, get_current_user

router = APIRouter(prefix="/notifications")


class NotificationItem(BaseModel):
    notification_id: str
    application_id: str | None = None
    reminder_type: str
    title: str
    content: str
    deadline: datetime | None = None
    sent_at: datetime
    read: bool


def _require_candidate(current_user: dict) -> None:
    if current_user.get("role") != "student":
        raise HTTPException(status_code=403, detail="该通知接口仅供候选人使用")


@router.get("")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    _require_candidate(current_user)
    unread_clause = " AND read_at IS NULL" if unread_only else ""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(f"""
            SELECT id, application_id, reminder_type, title, content,
                   deadline, sent_at, read_at
            FROM notifications
            WHERE tenant_id = :tenant_id AND recipient_id = :recipient_id
              AND channel = 'in_app' {unread_clause}
            ORDER BY created_at DESC LIMIT :limit
        """), {
            "tenant_id": current_user["tenant_id"],
            "recipient_id": current_user["user_id"],
            "limit": limit,
        })).mappings().all()
        unread_count = (await session.execute(text("""
            SELECT COUNT(*) FROM notifications
            WHERE tenant_id = :tenant_id AND recipient_id = :recipient_id
              AND channel = 'in_app' AND read_at IS NULL
        """), {
            "tenant_id": current_user["tenant_id"],
            "recipient_id": current_user["user_id"],
        })).scalar_one()
    return {
        "items": [NotificationItem(
            notification_id=str(row["id"]),
            application_id=str(row["application_id"]) if row["application_id"] else None,
            reminder_type=row["reminder_type"], title=row["title"], content=row["content"],
            deadline=row["deadline"], sent_at=row["sent_at"], read=row["read_at"] is not None,
        ) for row in rows],
        "unread_count": unread_count,
    }


@router.post("/{notification_id}/read", response_model=NotificationItem)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
):
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = (await session.execute(text("""
                UPDATE notifications SET read_at = COALESCE(read_at, NOW())
                WHERE id = :id AND tenant_id = :tenant_id
                  AND recipient_id = :recipient_id AND channel = 'in_app'
                RETURNING id, application_id, reminder_type, title, content,
                          deadline, sent_at, read_at
            """), {
                "id": str(notification_id),
                "tenant_id": current_user["tenant_id"],
                "recipient_id": current_user["user_id"],
            })).mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="通知不存在")
    return NotificationItem(
        notification_id=str(row["id"]),
        application_id=str(row["application_id"]) if row["application_id"] else None,
        reminder_type=row["reminder_type"], title=row["title"], content=row["content"],
        deadline=row["deadline"], sent_at=row["sent_at"], read=True,
    )
