"""招聘任务站内提醒。

本服务不常驻、不启动后台线程；部署环境应按固定周期调用一次性扫描脚本。
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


REMINDER_SQL = """
WITH due_tasks AS (
    SELECT a.id AS application_id, a.tenant_id, a.candidate_id,
           p.title AS position_title, task.task_type, task.deadline,
           CASE
             WHEN task.deadline <= NOW() + INTERVAL '2 hours' THEN 2
             ELSE 24
           END AS hours_before
    FROM recruitment_applications a
    JOIN job_positions p ON p.id = a.position_id AND p.tenant_id = a.tenant_id
    CROSS JOIN LATERAL (VALUES
        ('exam', a.exam_status, a.exam_deadline),
        ('interview', a.interview_status, a.interview_deadline)
    ) AS task(task_type, task_status, deadline)
    WHERE task.task_status IN ('available', 'in_progress')
      AND task.deadline IS NOT NULL
      AND task.deadline > NOW()
      AND task.deadline <= NOW() + INTERVAL '24 hours'
), inserted AS (
    INSERT INTO notifications (
        tenant_id, recipient_id, application_id, channel, reminder_type,
        title, content, deadline, idempotency_key
    )
    SELECT tenant_id, candidate_id, application_id, 'in_app',
           task_type || '_' || hours_before || 'h',
           CASE task_type WHEN 'exam' THEN '在线笔试即将截止' ELSE 'AI 面试即将截止' END,
           position_title || '的' ||
             CASE task_type WHEN 'exam' THEN '在线笔试' ELSE 'AI 面试' END ||
             '将在截止时间前关闭，请及时完成。',
           deadline,
           application_id::text || ':in_app:' || task_type || '_' ||
             hours_before || 'h:' || deadline::text
    FROM due_tasks
    WHERE hours_before = 2
       OR deadline > NOW() + INTERVAL '2 hours'
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id
)
SELECT COUNT(*) FROM inserted
"""


class NotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def generate_due_task_reminders(self) -> int:
        """生成 24 小时/2 小时提醒，唯一键保证重复扫描不会重复写入。"""
        result = await self.session.execute(text(REMINDER_SQL))
        return int(result.scalar_one())

    async def expire_overdue_tasks(self) -> int:
        """将已过截止时间且未完成的任务标记为过期。"""
        result = await self.session.execute(text("""
            UPDATE recruitment_applications
            SET exam_status = CASE
                    WHEN exam_status IN ('available', 'in_progress')
                     AND exam_deadline < NOW() THEN 'expired' ELSE exam_status END,
                interview_status = CASE
                    WHEN interview_status IN ('available', 'in_progress')
                     AND interview_deadline < NOW() THEN 'expired' ELSE interview_status END,
                updated_at = NOW()
            WHERE (exam_status IN ('available', 'in_progress') AND exam_deadline < NOW())
               OR (interview_status IN ('available', 'in_progress') AND interview_deadline < NOW())
            RETURNING id
        """))
        return len(result.fetchall())
