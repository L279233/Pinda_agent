from pathlib import Path

from backend.core.notification_service import REMINDER_SQL


def test_reminder_sql_has_two_windows_and_idempotency():
    assert "INTERVAL '24 hours'" in REMINDER_SQL
    assert "INTERVAL '2 hours'" in REMINDER_SQL
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in REMINDER_SQL
    assert "task.task_status IN ('available', 'in_progress')" in REMINDER_SQL


def test_notification_and_audit_schema_are_kept_in_sync():
    root = Path(__file__).resolve().parents[2]
    init_sql = (root / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    migrations = (root / "backend" / "db" / "migrations.py").read_text(encoding="utf-8")
    for name in ("notifications", "recruitment_decision_audits", "idempotency_key"):
        assert name in init_sql
        assert name in migrations
