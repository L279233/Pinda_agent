"""Check infrastructure readiness without modifying data."""
import asyncio
import sys
import urllib.request
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import get_settings


async def check_postgres(settings) -> str:
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )
    try:
        conn = await asyncpg.connect(dsn, timeout=3)
        count = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
        )
        await conn.close()
        return f"PostgreSQL OK ({count} public tables)"
    except Exception as exc:
        return f"PostgreSQL FAIL: {exc}"


def check_milvus(settings) -> str:
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=f"http://{settings.milvus_host}:{settings.milvus_port}")
        collections = client.list_collections()
        return f"Milvus OK ({len(collections)} collections)"
    except Exception as exc:
        return f"Milvus FAIL: {exc}"


def check_http(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return f"HTTP OK ({response.status})"
    except Exception as exc:
        return f"HTTP FAIL: {exc}"


async def main() -> None:
    settings = get_settings()
    health_host = "localhost" if settings.app_host == "0.0.0.0" else settings.app_host
    print(await check_postgres(settings))
    print(check_milvus(settings))
    print(check_http(f"http://{health_host}:{settings.app_port}/health"))
    print("Environment check complete; no data was changed.")


if __name__ == "__main__":
    asyncio.run(main())
