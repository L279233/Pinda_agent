# backend/api/v1/qa.py

import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse
from langchain_core.messages import HumanMessage

from backend.agents.qa.graph import build_qa_graph
from backend.core.memory import build_config, build_thread_id
from backend.dependencies import get_current_user
from backend.core.logger import get_logger
from backend.core.application_service import ApplicationService
from backend.core.exceptions import RecruitmentError
from backend.dependencies import AsyncSessionLocal

router = APIRouter()
logger = get_logger(__name__)


# ── 请求 / 响应模型 ───────────────────────────────────────────

class ChatRequest(BaseModel):
    """客户端发送聊天请求的数据结构"""
    session_id:        str        = Field(..., description="会话 ID，用于区分不同对话窗口")
    application_id:    uuid.UUID | None = Field(None, description="招聘申请 ID（查询本人任务时传入）")
    position_id:       uuid.UUID | None = Field(None, description="岗位 ID（可选），限定招聘资料范围")
    message:           str        = Field(..., min_length=1, max_length=2000, description="用户提问内容")
    enable_web_search: bool       = Field(False, description="低置信度时是否启用 Web 搜索补充")


class ChatResponse(BaseModel):
    """非流式聊天的响应结构"""
    session_id:    str
    answer:        str          # 最终回答文本
    answer_mode:   str          # "rag" / "web_augmented" / "llm_direct" / "general"
    confidence:    float        # 知识库检索置信度（0~1）
    sources:       list[str]    # 来源标注（文档名称或 URL）
    fallback_used: bool         # 是否触发了降级（如 Web 搜索或纯 LLM 回答）


class SessionMessage(BaseModel):
    """单条历史消息"""
    role:       str   # "user" 或 "assistant"
    content:    str
    created_at: str   # 时间戳（目前未使用，保留字段）


class HistoryResponse(BaseModel):
    """历史记录响应"""
    session_id:  str
    messages:    list[SessionMessage]
    summary:     str | None           # 由 save_memory_node 生成的对话摘要
    total_turns: int                  # 用户提问的总次数


async def resolve_qa_context(req: ChatRequest, current_user: dict) -> tuple[str | None, dict | None]:
    """校验申请/岗位归属，并返回可注入 QA 的候选人安全上下文。"""
    requested_position_id = str(req.position_id) if req.position_id else None
    application_id = str(req.application_id) if req.application_id else None
    role = current_user.get("role", "student")

    async with AsyncSessionLocal() as session:
        if application_id:
            row = (await session.execute(
                text("""
                    SELECT a.position_id, a.candidate_id
                    FROM recruitment_applications AS a
                    WHERE a.id = :application_id AND a.tenant_id = :tenant_id
                """),
                {"application_id": application_id,
                 "tenant_id": current_user["tenant_id"]},
            )).mappings().fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="招聘申请不存在")
            if role == "student" and str(row["candidate_id"]) != current_user["user_id"]:
                raise HTTPException(status_code=403, detail="无权访问该招聘申请")

            position_id = str(row["position_id"])
            if requested_position_id and requested_position_id != position_id:
                raise HTTPException(status_code=400, detail="岗位与招聘申请不匹配")

            task_context = None
            if role == "student":
                try:
                    view = await ApplicationService(session).get_candidate_tasks(
                        application_id=application_id,
                        candidate_id=current_user["user_id"],
                        tenant_id=current_user["tenant_id"],
                    )
                    task_context = view.model_dump(mode="json")
                except RecruitmentError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
            return position_id, task_context

        if requested_position_id:
            row = (await session.execute(
                text("""
                    SELECT id, status FROM job_positions
                    WHERE id = :position_id AND tenant_id = :tenant_id
                """),
                {"position_id": requested_position_id,
                 "tenant_id": current_user["tenant_id"]},
            )).mappings().fetchone()
            if not row or (role == "student" and row["status"] != "open"):
                raise HTTPException(status_code=404, detail="可查询的招聘岗位不存在")

    return requested_position_id, None


def build_initial_state(
    req: ChatRequest, current_user: dict, position_id: str | None,
    task_context: dict | None,
) -> dict:
    """构造 QA 图初始状态，旧 student_id 字段继续承载候选人 ID。"""
    return {
        "messages": [HumanMessage(content=req.message)],
        "student_id": current_user["user_id"],
        "tenant_id": current_user["tenant_id"],
        "session_id": req.session_id,
        "application_id": str(req.application_id) if req.application_id else None,
        "position_id": position_id,
        "candidate_task_context": task_context,
        "query_type": "PRECISE",
        "question_scope": "general",
        "enable_web_search": req.enable_web_search,
        "web_search_results": [],
    }


# ─────────────────── 路由函数开始 ─────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),  # 依赖注入：从 JWT 解析用户信息
):
    """
    智能问答（非流式）：发送用户消息，等待完整回答后返回。

    流程：
        1. 构建 LangGraph 工作流实例。
        2. 生成 thread_id（用户+会话唯一标识）。
        3. 组装初始状态（包含用户消息、用户信息等）。
        4. 调用 graph.ainvoke 执行整张图（同步等待完成）。
        5. 从结果中提取答案、模式、置信度、来源等，封装为 ChatResponse 返回。
    """
    position_id, task_context = await resolve_qa_context(req, current_user)
    # ① 获取编译好的 QA 图（单例，包含所有节点和边定义）
    graph = build_qa_graph()
    # ② 生成线程 ID，用于 LangGraph 的 MemorySaver 持久化（按用户与会话隔离）
    thread_id = build_config(current_user["user_id"], req.session_id, "qa")["configurable"]["thread_id"]

    # ③ 初始状态（符合 QAState 定义）
    initial_state = build_initial_state(req, current_user, position_id, task_context)
    # ④ 配置，传入 thread_id 让 MemorySaver 知道加载/保存哪个线程的状态
    config: dict = {"configurable": {"thread_id": thread_id}}

    # ⑤ 执行图（非流式，阻塞直到图运行到 END）
    try:
        result = await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        # 记录异常日志，并返回 500 错误
        logger.error("chat.invoke_error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "AGENT_ERROR", "message": str(e)},
        )

    # ⑥ 从结果中提取字段，构造响应对象
    return ChatResponse(
        session_id=req.session_id,
        answer=result.get("answer", ""),
        answer_mode=result.get("answer_mode", "llm_direct"),
        confidence=result.get("confidence", 0.0),
        sources=result.get("sources", []),
        fallback_used=result.get("fallback_used", False),
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    流式智能问答（SSE）：与 /chat 相同入参，但以 Server-Sent Events 流式返回。

    事件类型：
        - progress: 节点进度提示（如"理解问题中..."）
        - token:    LLM 生成的逐字 token（打字机效果）
        - meta:     流结束时附加的元数据（模式、置信度、来源）
        - done:     流结束标志
        - error:    异常信息（若发生）
    """
    # 同非流式：获取图、线程 ID、初始状态、配置
    position_id, task_context = await resolve_qa_context(req, current_user)
    graph = build_qa_graph()
    thread_id = build_config(current_user["user_id"], req.session_id, "qa")["configurable"]["thread_id"]

    initial_state = build_initial_state(req, current_user, position_id, task_context)
    config: dict = {"configurable": {"thread_id": thread_id}}

    # 只对这些生成节点产生的 LLM token 进行流式输出
    _GENERATE_NODES = {"generate_rag", "generate_direct", "generate_general"}

    # 节点开始时的友好进度标签（映射到中文）
    _PROGRESS_LABELS = {
        "classify_query":      "理解问题中...",
        "hyde_generate":       "理解问题中...",
        "multi_query_rewrite": "改写查询中...",
        "retrieve":            "召回相关文档...",
        "web_search":          "搜索互联网...",
        "generate_general":    "思考中...",
    }

    # 异步生成器，产出 SSE 事件（格式为 {"data": "<json>"}）
    async def event_generator():
        answer_mode = "llm_direct"   # 默认值
        confidence  = 0.0
        sources: list[str] = []
        emitted_answer = False

        try:
            # 使用 astream_events 获取细粒度事件流（v2 版本）
            async for event in graph.astream_events(
                initial_state, config=config, version="v2"
            ):
                evt  = event["event"]                     # 事件类型，如 "on_chain_start"
                node = event.get("metadata", {}).get("langgraph_node", "")  # 当前节点名

                # ① 节点开始 → 推送进度信息
                if evt == "on_chain_start" and node in _PROGRESS_LABELS:
                    yield {
                        "data": json.dumps(
                            {"type": "progress", "stage": _PROGRESS_LABELS[node]},
                            ensure_ascii=False,   # 中文不转义
                        )
                    }

                # ② LLM 流式 token → 仅来自三个生成节点
                elif evt == "on_chat_model_stream" and node in _GENERATE_NODES:
                    chunk = event["data"].get("chunk")
                    if chunk and chunk.content:
                        emitted_answer = True
                        yield {
                            "data": json.dumps(
                                {"type": "token", "content": chunk.content},
                                ensure_ascii=False,
                            )
                        }

                # ③ 生成节点结束 → 捕获最终的元数据（模式、来源、置信度）
                elif evt == "on_chain_end" and node in _GENERATE_NODES:
                    output = event["data"].get("output", {})
                    if isinstance(output, dict):
                        if output.get("answer") and not emitted_answer:
                            emitted_answer = True
                            yield {
                                "data": json.dumps(
                                    {"type": "token", "content": output["answer"]},
                                    ensure_ascii=False,
                                )
                            }
                        _mode = output.get("answer_mode")
                        if _mode:
                            answer_mode = _mode
                        _srcs = output.get("sources")
                        if _srcs is not None:
                            sources = _srcs
                        _conf = (output.get("structured_output") or {}).get("confidence")
                        if _conf is not None:
                            confidence = _conf

        except Exception as e:
            # 流式过程中发生异常 → 推送错误事件，并终止流
            logger.error("chat_stream.error", error=str(e), exc_info=True)
            yield {
                "data": json.dumps(
                    {"type": "error", "message": "流式输出异常，请使用普通接口重试"},
                    ensure_ascii=False,
                )
            }
            return

        # ④ 所有 token 推送完毕后，发送完整的元数据帧
        yield {
            "data": json.dumps(
                {
                    "type":        "meta",
                    "session_id":  req.session_id,
                    "answer_mode": answer_mode,
                    "confidence":  confidence,
                    "sources":     sources,
                },
                ensure_ascii=False,
            )
        }
        # ⑤ 发送结束标志
        yield {"data": json.dumps({"type": "done"})}

    # 使用 sse_starlette 将生成器包装为 SSE 响应
    return EventSourceResponse(event_generator())


@router.get("/sessions/{session_id}/history", response_model=HistoryResponse)
async def get_session_history(
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    获取指定会话的历史消息和摘要。

    数据来源：
        1. 摘要（summary）：从数据库表 qa_sessions 读取（由 save_memory_node 保存）。
        2. 完整消息列表：从 LangGraph 的 MemorySaver（检查点）中读取该线程的 messages 字段。

    注意：
        - 如果 MemorySaver 中没有该线程的记录，messages 返回空列表。
        - created_at 字段暂未填充，留作扩展。
    """
    from sqlalchemy import text as sa_text
    from langchain_core.messages import HumanMessage as LCHuman, AIMessage as LCAi
    from backend.dependencies import AsyncSessionLocal

    student_id = current_user["user_id"]
    db_thread_id = build_thread_id(student_id, session_id)

    # ── ① 读取摘要（从 PostgreSQL） ─────────────────────────
    summary = None
    try:
        async with AsyncSessionLocal() as db_session:
            # 使用原生 SQL 查询 qa_sessions 表
            result = await db_session.execute(
                sa_text(
                    "SELECT summary FROM qa_sessions "
                    "WHERE thread_id = :tid AND student_id = :sid "
                    "AND tenant_id = :tenant_id"
                ),
                {"tid": db_thread_id, "sid": student_id,
                 "tenant_id": current_user["tenant_id"]},
            )
            row = result.fetchone()
            if row:
                summary = row[0]
    except Exception as e:
        # 读取摘要失败仅记录警告，不影响返回历史消息
        logger.warning("get_history.db_error", error=str(e))

    # ── ② 读取消息列表（从 MemorySaver 检查点） ──────────────
    messages: list[SessionMessage] = []
    total_turns = 0
    try:
        # 重新构建图实例（与 chat 中的图一致）
        graph = build_qa_graph()
        config = build_config(student_id, session_id, "qa")
        # 获取该线程的最新状态（包含所有消息）
        state = await graph.aget_state(config)
        if state and state.values:
            # 遍历 messages 字段（列表）
            for msg in state.values.get("messages", []):
                # 提取文本内容（兼容 .text 或 .content）
                content = (
                    msg.text
                    if hasattr(msg, "text") and not callable(msg.text)
                    else str(msg.content)
                )
                if isinstance(msg, LCHuman):
                    messages.append(SessionMessage(role="user", content=content, created_at=""))
                elif isinstance(msg, LCAi):
                    messages.append(SessionMessage(role="assistant", content=content, created_at=""))
            # 计算用户提问次数（即 user 角色的消息数）
            total_turns = sum(1 for m in messages if m.role == "user")
    except Exception as e:
        logger.warning("get_history.checkpoint_error", error=str(e))

    return HistoryResponse(
        session_id=session_id,
        messages=messages,
        summary=summary,
        total_turns=total_turns,
    )
