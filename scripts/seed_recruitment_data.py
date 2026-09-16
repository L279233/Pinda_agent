"""Seed a minimal recruitment fixture after the schema and standard exam exist.

This creates one clearly marked local-development position only. It does not add
company policies or knowledge-base documents; those must come from real files.
"""
import asyncio
import asyncpg
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.config import get_settings

POSITION_ID = "b0000001-0000-0000-0000-000000000001"
EXAM_ID = "e0000001-0000-0000-0000-000000000001"


async def main() -> None:
    settings = get_settings()
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        exam_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM exams WHERE id=$1 AND tenant_id=$2)",
            EXAM_ID, settings.default_tenant_id,
        )
        if not exam_exists:
            raise RuntimeError(
                "标准试卷不存在，请先运行 scripts/seed_standard_exam.py"
            )
        position_id = await conn.fetchval(
            """
            INSERT INTO job_positions
                (id, tenant_id, code, title, department, jd_text, requirements,
                 evaluation_weights, resume_pass_score, exam_id, status)
            VALUES ($1, $2, 'AI-ENGINEER-DEMO', '大模型应用开发工程师（本地测试）',
                    '研发中心',
                    '负责 RAG、多智能体编排和招聘系统工程化落地。',
                    '{"skills":["Python","LangGraph","RAG"]}',
                    '{"resume":0.3,"exam":0.3,"interview":0.4}',
                    70, $3, 'open')
            ON CONFLICT (tenant_id, code) DO UPDATE SET
                title=EXCLUDED.title, jd_text=EXCLUDED.jd_text,
                requirements=EXCLUDED.requirements, exam_id=EXCLUDED.exam_id,
                status=EXCLUDED.status, updated_at=NOW()
            RETURNING id
            """,
            POSITION_ID, settings.default_tenant_id, EXAM_ID,
        )
        print(f"招聘测试岗位已就绪：{position_id}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
