# scripts/test_mcp_integrated.py
# 前提：uvicorn backend.main:app 已在 8000 端口运行

import asyncio, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env.local")

from backend.mcp.client import call_mcp_tool

async def main():
    # ── 知识库 MCP（路径前缀 /mcp/kb，实际端点 /mcp/kb/mcp）────────
    kb_results = await call_mcp_tool(
        server_url="http://localhost:8000/mcp/kb",      # 集成模式：带路径前缀
        tool_name="search_knowledge_base",
        arguments={"query": "AI课程大纲", "tenant_id": "tenant_default"},
    )
    print(f"[KB] 命中 {len(kb_results)} 条")
    for i, r in enumerate(kb_results, 1):
        print(f"  [{i}] score={r['score']:.4f}  {r['source_name']}")

    print()

    # ── Web 搜索 MCP（路径前缀 /mcp/search，实际端点 /mcp/search/mcp）─
    search_results = await call_mcp_tool(
        server_url="http://localhost:8000/mcp/search",  # 集成模式：带路径前缀
        tool_name="web_search",
        arguments={"query": "BGE-M3 向量模型", "max_results": 3},
    )
    print(f"[Search] 搜索结果 {len(search_results)} 条")
    for i, r in enumerate(search_results, 1):
        print(f"  [{i}] {r['title']}")

asyncio.run(main())