# scripts/test_mcp_search.py
# 前提：python backend/mcp/web_search_server.py 已在终端 1 运行

import asyncio, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env.local")

from backend.mcp.client import call_mcp_tool, list_mcp_tools

async def main():
    base = "http://localhost:8002"          # 独立模式：直接指向端口，无路径前缀

    tools = await list_mcp_tools(base)
    print(f"已注册工具：{[t['name'] for t in tools]}\n")

    results = await call_mcp_tool(
        server_url=base,
        tool_name="web_search",
        arguments={"query": "什么是牛奶", "max_results": 3},
    )
    print(f"搜索结果 {len(results)} 条")
    print(results)
    for i, r in enumerate(results, 1):
        print(f"[{i}] {r}")
        print(f"[{i}] {r['title']}")
        print(f"     {r['url']}")
        print(f"     {r['snippet'][:80]}...\n")

asyncio.run(main())