"""招聘知识库文档的后台入库任务。"""

import asyncio
from pathlib import Path

from sqlalchemy import text

from backend.core.logger import get_logger
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)
_ingestion_lock = asyncio.Lock()


async def ingest_knowledge_document(
    *,
    document_id: str,
    file_path: str,
    tenant_id: str,
    document_type: str,
    position_id: str | None,
    use_context: bool,
) -> None:
    """在线程中执行文档切分和向量化，并把处理状态写回 PostgreSQL。"""
    try:
        async with _ingestion_lock:
            def run_pipeline() -> int:
                from scripts.build_knowledge_base import build_pipeline

                return asyncio.run(build_pipeline(
                    file_path=file_path,
                    course_id=position_id or document_id,
                    document_id=document_id,
                    tenant_id=tenant_id,
                    use_context=use_context,
                    document_type=document_type,
                    position_id=position_id,
                ))

            chunk_count = await asyncio.to_thread(run_pipeline)

        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                UPDATE knowledge_documents
                SET status = 'ready', chunk_count = :chunk_count,
                    error_msg = NULL, updated_at = NOW()
                WHERE id = :document_id AND tenant_id = :tenant_id
            """), {
                "chunk_count": chunk_count,
                "document_id": document_id,
                "tenant_id": tenant_id,
            })
            await session.commit()
        logger.info(
            "knowledge_document.ready",
            document_id=document_id,
            chunk_count=chunk_count,
        )
    except Exception as exc:
        logger.exception(
            "knowledge_document.failed",
            document_id=document_id,
            error=str(exc),
        )
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                UPDATE knowledge_documents
                SET status = 'failed', error_msg = :error_msg, updated_at = NOW()
                WHERE id = :document_id AND tenant_id = :tenant_id
            """), {
                "error_msg": str(exc)[:2000],
                "document_id": document_id,
                "tenant_id": tenant_id,
            })
            await session.commit()
    finally:
        Path(file_path).unlink(missing_ok=True)
