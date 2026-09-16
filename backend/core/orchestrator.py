# backend/core/orchestrator.py
# Orchestrator：Agent 编排服务
# 职责：接收 API 层请求 → 意图路由 → 单 Agent 直达 / 多 Agent 串联 Pipeline

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, AIMessage

from backend.core.logger import get_logger
from backend.core.retry import with_retry          # 重试降级装饰器（来自 3.5，不在本章重讲）
from backend.core.exceptions import (               # 统一异常体系（来自 3.3，不在本章重讲）
    AgentExecutionError,
    PipelineError,
    IntentRouteError,
)

logger = get_logger(__name__)


class ExecutionMode(str, Enum):
    """执行模式：决定一个请求怎么跑。"""
    SINGLE   = "single"     # 单 Agent 直达（最常见）
    PIPELINE = "pipeline"   # 多 Agent 串联（如求职全链路）
    CLARIFY  = "clarify"    # 澄清对话（意图不明，需追问）


class AgentType(str, Enum):
    """四个 Agent 的类型标识（继承 str，可直接当字符串用）。"""
    QA        = "qa"         # 智能问答（第 5 章）
    EXAM      = "exam"       # 试卷批改（第 6 章）
    RESUME    = "resume"     # 简历审查（第 4 章）
    INTERVIEW = "interview"  # 模拟面试（第 7 章）

class AgentRequest(BaseModel):
    """所有 Agent 请求的统一入参 Schema"""
    student_id:    str = Field(..., description="发起请求的学员 ID")
    tenant_id:     str = Field(default="tenant_default", description="租户 ID")
    session_id:    str = Field(..., description="会话 ID，用于 thread_id 拼接")
    agent_type:    AgentType = Field(..., description="目标 Agent 类型")
    user_message:  str = Field(..., description="用户输入的原始文本")
    context:       dict[str, Any] = Field(default_factory=dict, description="附加上下文（文件路径/历史数据等）")
    pipeline_mode: bool = Field(default=False, description="是否强制走串联 Pipeline")

    @property
    def thread_id(self) -> str:
        """LangGraph Checkpointer 使用的线程 ID（与各 Agent 的 build_thread_id 格式一致）"""
        return f"student_{self.student_id}_session_{self.session_id}"


class AgentResponse(BaseModel):
    """所有 Agent 响应的统一出参 Schema"""
    success:       bool = Field(..., description="执行是否成功")
    agent_type:    AgentType = Field(..., description="实际执行的 Agent 类型")
    content:       str = Field(default="", description="主要文本响应内容")
    structured:    Optional[dict[str, Any]] = Field(default=None, description="结构化数据（评分/报告等）")
    fallback_used: bool = Field(default=False, description="是否触发了降级处理")
    error_msg:     Optional[str] = Field(default=None, description="失败时的错误信息")
    metadata:      dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class PipelineResult(BaseModel):
    """多 Agent 串联 Pipeline 的聚合结果（8.3 用到）"""
    steps:    list[AgentResponse] = Field(default_factory=list, description="各步骤结果列表")
    combined: dict[str, Any] = Field(default_factory=dict, description="聚合后的最终数据")

class Orchestrator:
    """
    Agent 编排服务。

    职责：
        - 单 Agent 模式：直接调用对应 Agent 的 LangGraph 图
        - 串联 Pipeline 模式：按顺序调用多个 Agent，前序输出注入后序输入
        - 澄清对话模式：返回结构化追问，等待用户补充信息后重新路由

    所有 Agent 图在首次使用时懒加载，避免启动时全量加载占用资源。
    """

    def __init__(self):
        # Agent 图注册表（懒加载，key=AgentType，value=编译后的 LangGraph）
        self._agent_graphs: dict[AgentType, Any] = {}

        # 串联 Pipeline 定义（触发条件 → Agent 执行顺序）
        self._pipelines: dict[str, list[AgentType]] = {
            "job_preparation":  [AgentType.RESUME, AgentType.INTERVIEW],  # 求职全链路：先审简历再面试
            "code_deep_review": [AgentType.EXAM, AgentType.QA],           # 代码复盘：先批改再深讲
        }

        logger.info("orchestrator.initialized")

    def _get_agent_graph(self, agent_type: AgentType) -> Any:
        """
        懒加载 Agent 的 LangGraph 编译图。

        读取/写入：self._agent_graphs（图缓存字典）
        首次访问某 Agent 时才 import 并 build 它的图，之后复用缓存。
        各 Agent 图在对应章节（第 4-7 章）已实现，此处只负责按需取用。
        """
        if agent_type not in self._agent_graphs:        # 缓存里没有 → 首次加载
            if agent_type == AgentType.QA:
                from backend.agents.qa.graph import build_qa_graph          # 第 5 章
                self._agent_graphs[agent_type] = build_qa_graph()

            elif agent_type == AgentType.EXAM:
                from backend.agents.exam.graph import build_exam_graph      # 第 6 章
                self._agent_graphs[agent_type] = build_exam_graph()

            elif agent_type == AgentType.RESUME:
                from backend.agents.resume.graph import build_resume_graph  # 第 4 章
                self._agent_graphs[agent_type] = build_resume_graph()

            elif agent_type == AgentType.INTERVIEW:
                from backend.agents.interview.graph import build_interview_graph  # 第 7 章
                self._agent_graphs[agent_type] = build_interview_graph()

            else:
                raise ValueError(f"未知 AgentType: {agent_type}")

            logger.info(
                "orchestrator.agent_graph_loaded",
                agent_type=agent_type.value,
            )

        return self._agent_graphs[agent_type]           # 返回（缓存的）编译图

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """
        统一请求处理入口。

        读取 request：pipeline_mode（决定单 Agent 还是 Pipeline）、agent_type、student_id
        返回：AgentResponse（统一响应格式，无论成功失败都返回它，不向上抛异常）

        流程：
            1. 判断执行模式（单 Agent / Pipeline）
            2. 分发到对应执行方法
            3. 返回统一格式的 AgentResponse
        """
        logger.info(
            "orchestrator.handle_start",
            agent_type=request.agent_type.value,
            student_id=request.student_id,
            pipeline_mode=request.pipeline_mode,
        )

        try:
            if request.pipeline_mode:                    # 强制串联模式
                # 前端"求职全流程辅导"按钮等直接触发
                result = await self._run_pipeline(request)        # 跑多 Agent（8.3）
                return self._aggregate_pipeline(result, request)  # 聚合成统一响应（8.3）
            else:                                        # 默认：单 Agent 直达
                return await self._run_single_agent(request)

        except Exception as e:                           # 兜底：任何异常都转成失败响应，不抛给上层
            logger.error(
                "orchestrator.handle_failed",
                agent_type=request.agent_type.value,
                error=str(e),
                exc_info=True,
            )
            return AgentResponse(
                success=False,
                agent_type=request.agent_type,
                content="系统处理请求时遇到问题，请稍后再试。",  # 给用户的友好兜底文案
                error_msg=str(e),
            )

    async def _run_single_agent(self, request: AgentRequest) -> AgentResponse:
        """
        单 Agent 直达模式：直接调用目标 Agent 的 LangGraph 图。

        读取 request：agent_type / user_message / student_id / tenant_id / session_id / context
        返回：AgentResponse（从 Agent 图最终 State 提取 content + structured + fallback_used）
        使用 with_retry 包装，自动处理重试和降级（来自 3.5）。
        """
        graph = self._get_agent_graph(request.agent_type)    # 懒加载取出目标 Agent 图

        # 构建 LangGraph 输入 State（统一三件套 + context 展开）
        initial_state = {
            "messages": [HumanMessage(content=request.user_message)],  # 用户消息
            "student_id": request.student_id,
            "tenant_id": request.tenant_id,
            "session_id": request.session_id,
            **request.context,                           # 附加上下文（如文件路径、简历结果）平铺进 State
        }

        config = {                                       # LangGraph 运行配置
            "configurable": {
                "thread_id": f"{request.agent_type.value}:{request.thread_id}",
            }
        }

        @with_retry(agent_type=request.agent_type.value)  # 给本次调用套上三层兜底（重试→降级→系统兜底）
        async def _invoke():
            return await graph.ainvoke(initial_state, config=config)  # 真正跑 Agent 图

        result_state = await _invoke()                   # 执行（失败时 with_retry 自动处理）

        # 从最终 State 提取响应内容：取最后一条消息的文本
        last_message = result_state["messages"][-1]
        content = last_message.text if hasattr(last_message, "text") else str(last_message.content)

        return AgentResponse(
            success=True,
            agent_type=request.agent_type,
            content=content,                             # 主文本响应
            structured=result_state.get("structured_output"),   # 结构化数据（报告/评分，若有）
            fallback_used=result_state.get("fallback_used", False),  # 是否走了降级
        )

    async def _run_pipeline(self, request: AgentRequest) -> PipelineResult:
        """
        多 Agent 串联 Pipeline 模式。

        读取 request：context["pipeline_key"]（选哪条 Pipeline）、student_id 等
        返回：PipelineResult（steps=各步 AgentResponse 列表）

        前序 Agent 的 structured_output 自动注入后序 Agent 的 context，
        已完成步骤的结果持久化保存，失败不丢弃前序成果。
        """
        # 根据 context 中的 pipeline_key 选择对应 Pipeline（默认求职全链路）
        pipeline_key = request.context.get("pipeline_key", "job_preparation")

        if pipeline_key not in self._pipelines:          # 未知 key → 抛 PipelineError（来自 3.3）
            raise PipelineError(f"未知 Pipeline 类型: {pipeline_key}")

        agent_sequence = self._pipelines[pipeline_key]   # 取出该 Pipeline 的 Agent 执行顺序
        result = PipelineResult()                        # 准备收集各步结果

        # 初始上下文：从请求的 context 拷一份，后续逐步往里塞前序结果
        current_context = dict(request.context)

        for idx, agent_type in enumerate(agent_sequence):  # 按序执行每个 Agent
            # 为这一步构造独立的 AgentRequest（session_id 加 _step{idx} 后缀，避免检查点串台）
            step_request = AgentRequest(
                student_id=request.student_id,
                tenant_id=request.tenant_id,
                session_id=f"{request.session_id}_step{idx}",
                agent_type=agent_type,
                user_message=request.user_message,
                context=current_context,                 # 带上累积的上下文（含前序结果）
                pipeline_mode=False,                     # 单步内不再触发 Pipeline，防止递归
            )

            logger.info(
                "orchestrator.pipeline_step_start",
                step=idx + 1,
                total=len(agent_sequence),
                agent_type=agent_type.value,
            )

            step_response = await self._run_single_agent(step_request)  # 复用单 Agent 直达跑这一步
            result.steps.append(step_response)           # 记录本步结果

            if not step_response.success:                # 本步失败 → 保留已完成成果，终止后续
                logger.warning(
                    "orchestrator.pipeline_step_failed",
                    step=idx + 1,
                    agent_type=agent_type.value,
                    error=step_response.error_msg,
                )
                break                                    # 不再跑后面的步骤（但前面的结果已保存）

            # ★ 上下文传递：把本步的结构化输出注入累积上下文，供下一步使用
            if step_response.structured:
                current_context[f"{agent_type.value}_result"] = step_response.structured
                logger.info(
                    "orchestrator.pipeline_context_passed",
                    from_agent=agent_type.value,
                    keys=list(step_response.structured.keys()),
                )

        return result                                    # 返回各步聚合前的原始结果
    def _aggregate_pipeline(
        self,
        pipeline_result: PipelineResult,                # 各步结果
        request: AgentRequest,                          # 原始请求
    ) -> AgentResponse:
        """
        聚合 Pipeline 各步骤结果，返回统一 AgentResponse。

        读取 pipeline_result.steps（各步 AgentResponse）
        返回 AgentResponse：content=各步文本拼接，structured=combined（含每步明细）
        combined 字段包含所有步骤的结构化数据。
        """
        combined = {}                                    # 汇总每步的结构化明细
        all_contents = []                                # 收集每步的文本，最后拼接
        any_success = False                              # 只要有一步成功，整体就算部分成功

        for idx, step in enumerate(pipeline_result.steps):
            step_key = f"step_{idx + 1}"                 # step_1 / step_2 ...
            combined[step_key] = {                       # 记录这一步的元信息
                "agent_type": step.agent_type.value,
                "success": step.success,
                "structured": step.structured,
            }
            if step.success:
                any_success = True
                if step.content:
                    all_contents.append(step.content)    # 成功且有文本 → 收进待拼接列表

        return AgentResponse(
            success=any_success,                         # 任一步成功即 True
            agent_type=request.agent_type,
            content="\n\n---\n\n".join(all_contents),    # 各步文本用分隔线拼成一段
            structured=combined,                         # 结构化里带每步明细
            fallback_used=any(s.fallback_used for s in pipeline_result.steps),  # 任一步降级即标记
        )

# ──────────────────────────────────────────────────────────────
# 模块级单例（应用生命周期内复用）
# ──────────────────────────────────────────────────────────────
_orchestrator_instance: Optional[Orchestrator] = None    # 全局唯一实例（初始为空）


def get_orchestrator() -> Orchestrator:
    """获取 Orchestrator 单例（FastAPI 依赖注入使用）"""
    global _orchestrator_instance
    if _orchestrator_instance is None:                   # 第一次调用才创建
        _orchestrator_instance = Orchestrator()
    return _orchestrator_instance                        # 之后都返回同一个实例
