#!/usr/bin/env python3
"""
demo_nodes.py - 手动演示每个节点函数的输入输出（无外部依赖）
直接运行：python demo_nodes.py
"""
import asyncio
from langchain_core.messages import HumanMessage, AIMessage

# 导入所有节点函数
from backend.agents.qa.nodes import (
    classify_query_node,
    hyde_generate_node,
    multi_query_rewrite_node,
    retrieve_node,
    generate_rag_node,
    web_search_node,
    generate_direct_node,
    generate_general_node,
    enqueue_pending_node,
    save_memory_node,
)


# ---------- 辅助：构造基础 state ----------
def base_state(extra=None):
    s = {
        "student_id": "stu_001",
        "tenant_id": "ten_001",
        "session_id": "sess_123",
        "position_id": "position_ai",
        "application_id": None,
        "candidate_task_context": None,
        "question_scope": "general",
        "messages": [],
        "original_query": "",
        "query_type": "PRECISE",
        "rewritten_queries": [],
        "hyde_document": None,
        "ranked_chunks": [],
        "confidence": 0.0,
        "is_high_confidence": False,
        "web_search_results": [],
        "answer": "",
        "sources": [],
        "answer_mode": "",
        "existing_summary": None,
        "should_summarize": False,
        "enable_web_search": False,
        "fallback_used": False,
        "structured_output": None,
    }
    if extra:
        s.update(extra)
    return s


# ---------- 演示每个节点 ----------
async def demo_classify_query():
    print("\n========== classify_query_node ==========")
    state = base_state({"messages": [HumanMessage(content="什么是过拟合")]})
    result = await classify_query_node(state)
    print("输入 query:", state["messages"][-1].content)
    print("输出 query_type:", result["query_type"])
    print("输出 original_query:", result["original_query"])
    print("完整返回:", result)


async def demo_hyde_generate():
    print("\n========== hyde_generate_node ==========")
    state = base_state({
        "original_query": "反向传播为什么重要",
        "messages": [HumanMessage(content="反向传播为什么重要")],
    })

    result = await hyde_generate_node(state)
    print("输入 query:", state["original_query"])
    print("输出 hyde_document:", result["hyde_document"][:80] + "...")


async def demo_multi_query_rewrite():
    print("\n========== multi_query_rewrite_node ==========")
    state = base_state({
        "original_query": "梯度消失",
        "messages": [
            HumanMessage(content="梯度消失"),
            AIMessage(content="之前我们讨论了反向传播"),
        ],
    })

    result = await multi_query_rewrite_node(state)
    print("输入 query:", state["original_query"])
    print("输出 rewritten_queries:", result["rewritten_queries"])


async def demo_retrieve():
    print("\n========== retrieve_node ==========")
    # 模拟 PRECISE 分支
    state = base_state({
        "query_type": "PRECISE",
        "original_query": "过拟合",
        "tenant_id": "ten_001",
        "position_id": "position_ai",
    })

    result = await retrieve_node(state)
    print("输入 query_type:", state["query_type"])
    print("输出 ranked_chunks 数量:", len(result["ranked_chunks"]))
    print("输出 confidence:", result["confidence"])
    print("输出 is_high_confidence:", result["is_high_confidence"])


async def demo_generate_rag():
    print("\n========== generate_rag_node ==========")
    state = base_state({
        "original_query": "过拟合",
        "messages": [HumanMessage(content="过拟合")],
        "ranked_chunks": [
            {"content": "过拟合是指模型在训练集上表现好但在测试集上表现差的现象。",
             "score": 0.87, "metadata": {"source_name": "课件"}}
        ],
        "confidence": 0.87,
        "existing_summary": None,
    })

    result = await generate_rag_node(state)
    print("输入 query:", state["original_query"])
    print("输出 answer 前50字:", result["answer"][:50] + "...")
    print("输出 sources:", result["sources"])
    print("输出 answer_mode:", result["answer_mode"])


async def demo_web_search():
    print("\n========== web_search_node ==========")
    state = base_state({"original_query": "最新AI新闻"})

    result = await web_search_node(state)
    print("输入 query:", state["original_query"])
    print("输出 web_search_results 数量:", len(result["web_search_results"]))
    if result["web_search_results"]:
        print("  第一条标题:", result["web_search_results"][0]["title"])


async def demo_generate_direct():
    print("\n========== generate_direct_node ==========")
    state = base_state({
        "original_query": "量子计算原理",
        "messages": [HumanMessage(content="量子计算原理")],
        "web_search_results": [],  # 无搜索结果
        "confidence": 0.3,
        "existing_summary": None,
    })

    result = await generate_direct_node(state)
    print("输入 query:", state["original_query"])
    print("输出 answer 前40字:", result["answer"][:40] + "...")
    print("输出 answer_mode:", result["answer_mode"])


async def demo_generate_general():
    print("\n========== generate_general_node ==========")
    state = base_state({
        "original_query": "你好",
        "messages": [HumanMessage(content="你好")],
        "web_search_results": [],
    })

    result = await generate_general_node(state)
    print("输入 query:", state["original_query"])
    print("输出 answer:", result["answer"])
    print("输出 answer_mode:", result["answer_mode"])


async def demo_enqueue_pending():
    print("\n========== enqueue_pending_node ==========")
    state = base_state({
        "original_query": "罕见问题",
        "student_id": "stu_001",
        "tenant_id": "ten_001",
        "confidence": 0.2,
    })


    result = await enqueue_pending_node(state)
    print("输入 query:", state["original_query"])
    print("输出 (总是空字典):", result)  # 纯副作用，返回{}


async def demo_save_memory():
    print("\n========== save_memory_node ==========")
    state = base_state({
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "student_id": "stu_001",
        "session_id": "sess_123",
        "tenant_id": "ten_001",
        "position_id": "position_ai",
        "existing_summary": None,
        "should_summarize": False,
    })

    result = await save_memory_node(state)
    print("输入 should_summarize:", state["should_summarize"])
    print("输出 (总是空字典):", result)  # 纯副作用


# ---------- 主函数 ----------
async def main():
    await demo_classify_query()
    await demo_hyde_generate()
    await demo_multi_query_rewrite()
    await demo_retrieve()
    await demo_generate_rag()
    await demo_web_search()
    await demo_generate_direct()
    await demo_generate_general()
    await demo_enqueue_pending()
    await demo_save_memory()
    print("\n========== 演示完成 ==========")


if __name__ == "__main__":
    asyncio.run(main())
