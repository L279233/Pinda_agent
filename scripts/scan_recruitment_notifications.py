"""一次性扫描招聘临期任务。

运行：python scripts/scan_recruitment_notifications.py
生产环境用 cron、Windows 任务计划或云调度每 5-15 分钟调用一次。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.notification_service import NotificationService
from backend.dependencies import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            service = NotificationService(session)
            reminders = await service.generate_due_task_reminders()
            expired = await service.expire_overdue_tasks()
    print(f"generated_reminders={reminders} expired_applications={expired}")


if __name__ == "__main__":
    asyncio.run(main())
