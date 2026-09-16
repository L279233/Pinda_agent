"""Read-only verification for local recruitment demo fixtures."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pymilvus import MilvusClient
from sqlalchemy import text

from backend.config import get_settings
from backend.dependencies import AsyncSessionLocal

POSITION_ID = "b0000001-0000-0000-0000-000000000001"
DOCUMENT_IDS = [
    "e2f44456-cbb8-5a83-8fb5-39958842f034",
    "8691c481-64ec-5f25-8400-4c7235371aa1",
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text("""
            SELECT p.title, COUNT(DISTINCT q.id) AS exam_questions,
                   COUNT(DISTINCT iq.id) AS interview_questions
            FROM job_positions p
            JOIN questions q ON q.exam_id = p.exam_id
            LEFT JOIN interview_questions iq
              ON iq.tenant_id = p.tenant_id AND iq.target_position = p.title
            WHERE p.id = :position
            GROUP BY p.title
        """), {"position": POSITION_ID})).mappings().one()
    settings = get_settings()
    client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")
    docs = client.query(
        collection_name="knowledge_domain",
        filter=f'document_id in ["{DOCUMENT_IDS[0]}", "{DOCUMENT_IDS[1]}"]',
        output_fields=["document_id", "position_id", "document_type"],
        limit=20,
    )
    if row["exam_questions"] != 5 or row["interview_questions"] != 5:
        raise RuntimeError(f"题库数量异常：{dict(row)}")
    if len(docs) != 5 or any(item["position_id"] != POSITION_ID for item in docs):
        raise RuntimeError(f"知识库测试数据异常：{docs}")
    types = sorted({item["document_type"] for item in docs})
    print(f"测试岗位：{row['title']}")
    print(f"笔试题：{row['exam_questions']}，面试题：{row['interview_questions']}")
    print(f"知识库 chunk：{len(docs)}，类型：{', '.join(types)}")


if __name__ == "__main__":
    asyncio.run(main())
