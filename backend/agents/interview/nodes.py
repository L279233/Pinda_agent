# backend/agents/interview/nodes.py

import asyncio
import json
import uuid

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from sqlalchemy import text

from backend.agents.interview.state import (
    InterviewState, InterviewStage, AnswerQuality, InterviewReport,
)
from backend.agents.interview.prompts import (
    SYSTEM_PROMPT, WARMUP_PROMPT, INTRO_EVAL_TECH_FIRST_PROMPT,
    TECH_BASE_PROMPT, PROJECT_PROMPT, CLOSING_PROMPT,
    STAGE_TRANSITION_PROMPTS, EVALUATE_ANSWER_PROMPT,
    EVALUATE_THINK_PROMPT, GENERATE_REPORT_PROMPT,
    TECH_BASE_GENERATE_PROMPT,
)
from langchain_core.messages import BaseMessage

from backend.core.llm_factory import get_llm, get_structured_llm
from backend.core.memory import (
    build_thread_id, should_trigger_summary,
    compress_to_summary, trim_messages_to_window,
)
from backend.core.logger import get_logger
from backend.core.application_service import ApplicationService
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)            # 本模块日志器

DEFAULT_MAX_TURNS = 40                    # 一场面试的总轮数上限（兜底防止无限进行）
MAX_FOLLOWUP_PER_QUESTION = 2            # 同一道题最多追问几次

# 各阶段「最少 / 最多」轮数（check_stage 用，详见 7.5）
STAGE_MIN_TURNS = {
    InterviewStage.WARMUP.value:    1,   # 热身至少 1 轮（自我介绍完即可推进）
    InterviewStage.TECH_BASE.value: 6,   # 技术环节至少 6 轮
    InterviewStage.PROJECT.value:   2,   # 项目环节至少 2 轮
    InterviewStage.CLOSING.value:   2,   # 反问环节至少 2 轮
}
STAGE_MAX_TURNS = {
    InterviewStage.WARMUP.value:    4,   # 热身最多 4 轮（避免卡住）
    InterviewStage.TECH_BASE.value: 12,  # 技术环节最多 12 轮
    InterviewStage.PROJECT.value:   14,  # 项目环节最多 14 轮
    InterviewStage.CLOSING.value:   4,   # 反问环节最多 4 轮
}


def _msg_text(msg: BaseMessage) -> str:
    """安全提取消息文本，兼容 str 和 list（多模态）content。"""
    content = msg.content                 # 取出消息内容（可能是 str 或 list）
    if isinstance(content, list):         # 多模态消息：content 是片段列表
        return "".join(                   # 把各片段的文本拼起来
            p.get("text", "") if isinstance(p, dict) else str(p)  # dict 取 text 字段，否则 str()
            for p in content
        )
    return str(content)                   # 普通字符串直接返回


async def _generate_questions_by_llm(
    target_position: str,
    job_description: str,
    job_requirements: dict,
    count: int = 8,
) -> list:
    """
    根据目标岗位用 LLM 动态生成技术面试题，作为 DB 题库的替代/补充。

    参数：target_position（岗位，决定出题方向）、count（题数，默认 8）
    返回：题目字典列表，每项 = {id, content, difficulty, tags, asked}；失败返回 []。
    """
    try:                                            # 出题失败不应让整个加载崩，包 try
        llm = get_llm("interview", temperature=0.3) # 取模型（温度 0.3，出题略有多样性）
        prompt = TECH_BASE_GENERATE_PROMPT.format(  # 拼出题 Prompt
            position=target_position,               # 填岗位
            job_description=job_description,
            job_requirements=json.dumps(job_requirements or {}, ensure_ascii=False),
            count=count,                            # 填题数
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])  # 调大模型
        text = _msg_text(response).strip()          # 安全取出回复文本

        # 兼容 LLM 有时用 ```json ... ``` 包裹输出：把里面以 [ 开头的那段抠出来
        if "```" in text:
            parts = text.split("```")               # 按围栏切开
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):     # 去掉可能的 "json" 语言标记
                    stripped = stripped[4:].strip()
                if stripped.startswith("["):        # 找到以 [ 开头的题目数组
                    text = stripped
                    break

        questions_raw = json.loads(text)            # 解析成 Python 列表
        result = []                                 # 收集规整后的题目
        for i, q in enumerate(questions_raw):       # 逐题处理
            if not isinstance(q, dict) or not q.get("content"):  # 跳过格式不对/无题干的
                continue
            result.append({                         # 规整成统一结构
                "id":         f"llm_{i}",           # LLM 题用 llm_0/llm_1... 当 id
                "content":    q["content"],         # 题干
                "difficulty": q.get("difficulty", "medium"),     # 难度（缺省 medium）
                "tags":       q.get("tags", [target_position]),  # 标签（缺省用岗位名）
                "asked":      False,                # 初始都没问过
            })
        logger.info("generate_questions_llm.done", count=len(result), position=target_position)
        return result                               # 返回题库
    except Exception as e:                          # 出题失败（LLM 报错/JSON 解析失败等）
        logger.warning("generate_questions_llm.failed", error=str(e))
        return []                                   # 返回空，让 load_context 降级到纯 DB 题库


async def load_context_node(state: InterviewState) -> dict:
    """
    上下文加载节点（每轮第一个执行）。

    读取 state：
        student_id / session_id        定位会话（拼 thread_id）
        total_turn_count               判断是否首轮（==0 即首轮）
        resume_review_id               首轮用它读简历联动数据（可为空）
        target_position                首轮用它生成/查询题库
        resume_projects / resume_skills 首轮的兜底初始值
    返回（合并进 state）：
        首轮：current_stage / 各计数 / question_bank / resume_projects / resume_skills
              / existing_summary 等一整套初始化
        非首轮：只刷新 existing_summary

    每轮都执行：加载历史摘要（from DB）。
    首轮额外执行：初始化 State、加载简历联动数据、从题库拉题。
    """
    student_id = state.get("student_id") or ""       # 取学员 ID
    session_id = state.get("session_id") or ""       # 取会话 ID
    if not student_id or not session_id:             # 缺 ID 无法定位会话 → 返回空
        logger.error("load_context.missing_ids", state_keys=list(state.keys()))
        return {}
    thread_id = build_thread_id(student_id, session_id)  # 拼 thread_id（摘要表/检查点的 key）

    is_first_turn = state.get("total_turn_count", 0) == 0  # 总轮数为 0 = 首轮
    updates: dict = {}                               # 收集要写回 state 的更新

    # ── 内部查询函数（各自独立 session，可并行执行）──────────

    async def _fetch_resume(resume_review_id: str) -> dict:
        # 按 resume_review_id 去 resume_reviews 表读简历结构化数据（第4章简历Agent写的）
        try:
            async with AsyncSessionLocal() as s:     # 独立 DB 会话
                result = await s.execute(
                    text("""
                        SELECT structured_data FROM resume_reviews
                        WHERE id = :id AND tenant_id = :tenant_id
                          AND student_id = :student_id AND status = 'done'
                    """),
                    {"id": resume_review_id, "tenant_id": state["tenant_id"],
                     "student_id": state["student_id"]},
                )
                row = result.mappings().fetchone()
            if row and row["structured_data"]:       # 读到数据
                structured = (                       # structured_data 可能已是 dict 或 JSON 字符串
                    row["structured_data"]
                    if isinstance(row["structured_data"], dict)
                    else json.loads(row["structured_data"])
                )
                return {                             # 只取项目和技能两项
                    "resume_projects": structured.get("projects", []),
                    "resume_skills":   structured.get("skills_list", []),
                }
        except Exception as e:                       # 读失败不影响面试，记日志返回空
            logger.warning("load_context.resume_load_failed", error=str(e))
        return {}

    async def _fetch_questions(target: str) -> list:
        # 从 interview_questions 表查岗位题库（匹配该岗位或 general 通用题）
        try:
            async with AsyncSessionLocal() as s:
                result = await s.execute(
                    text("""
                        SELECT id, content, difficulty, tags
                        FROM interview_questions
                        WHERE (target_position = :target OR target_position = 'general')
                          AND tenant_id = :tenant_id
                          AND is_active = TRUE
                        ORDER BY
                            CASE difficulty
                                WHEN 'medium' THEN 1
                                WHEN 'easy'   THEN 2
                                ELSE 3
                            END,
                            RANDOM()
                        LIMIT 20
                    """),
                    {"target": target, "tenant_id": state["tenant_id"]},
                )
                rows = result.mappings().all()
            return [                                 # 规整成统一题目结构
                {
                    "id":         str(r["id"]),
                    "content":    r["content"],
                    "difficulty": r["difficulty"],
                    "tags":       r["tags"] if isinstance(r["tags"], list) else (r["tags"] or []),
                    "asked":      False,             # 初始都没问过
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("load_context.question_bank_failed", error=str(e))
            return []

    async def _fetch_summary() -> str | None:
        # 读历史对话摘要（长对话压缩后存在 interview_sessions.summary）
        try:
            async with AsyncSessionLocal() as s:
                result = await s.execute(
                    text("""
                        SELECT summary FROM interview_sessions
                        WHERE thread_id = :tid AND tenant_id = :tenant_id
                          AND student_id = :student_id
                    """),
                    {"tid": thread_id, "tenant_id": state["tenant_id"],
                     "student_id": state["student_id"]},
                )
                row = result.mappings().fetchone()
            return row["summary"] if row else None
        except Exception as e:
            logger.warning("load_context.summary_load_failed", error=str(e))
            return None

    # ── 并行执行所有查询 ─────────────────────────────────────
    if is_first_turn:                                # 首轮：完整初始化
        updates.update({                             # 一次性写入所有起始字段
            "current_stage":       InterviewStage.WARMUP.value,   # 起始阶段 = 热身
            "stage_turn_count":    0,                # 当前阶段轮数清零
            "total_turn_count":    0,                # 总轮数清零
            "followup_count":      0,                # 追问次数清零
            "projects_asked":      [],               # 已深挖项目清空
            "last_answer_quality": AnswerQuality.ADEQUATE.value,  # 上轮质量给中性初值
            "max_turns":           DEFAULT_MAX_TURNS,# 轮数上限 40
            "report":              None,             # 报告未生成
            "should_summarize":    False,            # 暂不压缩
            "resume_projects":     state.get("resume_projects") or [],  # 简历项目兜底空
            "resume_skills":       state.get("resume_skills") or [],    # 简历技能兜底空
        })

        resume_review_id = state.get("resume_review_id")  # 简历联动 ID（可能为空）
        target = state.get("target_position", "")         # 目标岗位

        async def _noop_resume():                    # 没有简历 ID 时的占位协程（返回空）
            return {}

        # 四路 I/O 各起一个协程：简历 / DB题库 / LLM出题 / 历史摘要
        resume_coro    = _fetch_resume(resume_review_id) if resume_review_id else _noop_resume()
        questions_coro = _fetch_questions(target)
        llm_q_coro     = _generate_questions_by_llm(
            target,
            state.get("job_description", ""),
            state.get("job_requirements", {}),
        )
        summary_coro   = _fetch_summary()

        import asyncio
        # gather 同时发起四路、等全部完成（总耗时≈最慢的 LLM 出题，详见 7.4.4）
        resume_data, db_questions, llm_questions, existing_summary = await asyncio.gather(
            resume_coro, questions_coro, llm_q_coro, summary_coro
        )

        # LLM 生成题优先（岗位精准），DB 题兜底/补充
        if llm_questions:
            question_bank = llm_questions + (db_questions if db_questions else [])  # LLM 题在前，DB 题补后
        else:
            question_bank = db_questions             # LLM 出题失败 → 纯用 DB 题

        if resume_data:                              # 读到了简历联动数据
            updates["resume_projects"] = resume_data.get("resume_projects", [])
            updates["resume_skills"]   = resume_data.get("resume_skills", [])
            logger.info("load_context.resume_loaded", projects=len(updates["resume_projects"]))

        updates["question_bank"]    = question_bank  # 写入题库
        updates["existing_summary"] = existing_summary  # 写入历史摘要
        logger.info("load_context.question_bank_loaded", count=len(question_bank))
    else:                                            # 非首轮：只刷新摘要
        updates["existing_summary"] = await _fetch_summary()

    return updates                                   # 返回更新，写回 state


async def check_stage_node(state: InterviewState) -> dict:
    """
    阶段推进节点（纯逻辑，不调 LLM）：判断当前阶段是否要推进 / 强制结束。

    读取 state：
        current_stage / stage_turn_count / total_turn_count / max_turns / messages
        （TECH_BASE 还看 question_bank，PROJECT 还看 resume_projects/projects_asked）
    返回（写回 state）：
        推进时：{current_stage: 新阶段, stage_turn_count:0, followup_count:0, current_question:None}
        不推进：{}（空 dict，什么都不改）

    阶段推进条件：
        WARMUP → TECH_BASE: turns >= 1（学员完成自我介绍）
        TECH_BASE → PROJECT: stage_turns >= 6 或题库问完
        PROJECT → CLOSING:   stage_turns >= 2 或项目全覆盖
        任意 → FINISHED(强制): total_turns >= max-2 或发送结束关键词
        CLOSING → FINISHED:  stage_turns >= 2
    """
    current_stage = state.get("current_stage", InterviewStage.WARMUP.value)  # 当前阶段
    stage_turns   = state.get("stage_turn_count", 0)    # 当前阶段已进行轮数
    total_turns   = state.get("total_turn_count", 0)    # 总轮数
    max_turns     = state.get("max_turns", DEFAULT_MAX_TURNS)  # 轮数上限
    messages      = state.get("messages", [])           # 对话消息列表

    # 已结束则不再处理（直接交给后面的条件路由去生成报告）
    if current_stage == InterviewStage.FINISHED.value:
        return {}

    # 从后往前找学员最后一条消息（用于检测"结束面试"关键词）
    latest_student_msg = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):               # 找到第一条 Human 消息
            latest_student_msg = _msg_text(msg)
            break

    # 强制结束判断：总轮数快到上限，或学员主动说要结束 → 直接跳 FINISHED（跳过 CLOSING）
    force_end_keywords = ["结束面试", "不想继续", "面试结束", "结束吧", "到此为止"]
    is_forced_end = (
        total_turns >= max_turns - 2                    # 轮数 ≥ 上限-2
        or any(kw in latest_student_msg for kw in force_end_keywords)  # 或命中关键词
    )

    if is_forced_end:                                   # 触发强制结束
        logger.info("check_stage.forced_to_finished", reason="keyword_or_max_turns")
        return {"current_stage": InterviewStage.FINISHED.value}

    # CLOSING → FINISHED（反问环节满 2 轮就结束）
    if current_stage == InterviewStage.CLOSING.value:
        if stage_turns >= STAGE_MIN_TURNS[InterviewStage.CLOSING.value]:
            logger.info("check_stage.advance", from_stage="closing", to_stage="finished")
            return {"current_stage": InterviewStage.FINISHED.value}
        return {}                                       # 还没满 2 轮，继续 closing

    # 常规阶段推进：取当前阶段的最小/最大轮数
    stage_min = STAGE_MIN_TURNS.get(current_stage, 2)   # 至少问几轮才允许推进
    stage_max = STAGE_MAX_TURNS.get(current_stage, 10)  # 问满几轮就强制推进

    should_advance = False
    if stage_turns >= stage_max:                        # 到上限 → 强制推进
        should_advance = True
    elif stage_turns >= stage_min:                      # 到下限 → 看是否满足内容条件
        should_advance = _check_advance_condition(current_stage, state, latest_student_msg)

    if should_advance:                                  # 决定推进
        next_stage = _next_stage(current_stage)         # 算出下一阶段
        logger.info("check_stage.advance", from_stage=current_stage, to_stage=next_stage)
        return {
            "current_stage":    next_stage,             # 切到新阶段
            "stage_turn_count": 0,                      # 新阶段轮数从 0 开始
            "followup_count":   0,                      # 追问计数清零
            "current_question": None,                   # 清掉当前题，让新阶段重新选题
        }

    return {}                                           # 不推进，保持当前阶段

def _check_advance_condition(stage: str, state: InterviewState, latest_msg: str) -> bool:
    """判断当前阶段「内容上」是否满足推进条件（轮数已达下限后才调用）。"""
    if stage == InterviewStage.WARMUP.value:
        # WARMUP min=1，学员发任意消息即视为完成自我介绍，立即推进
        return True

    if stage == InterviewStage.TECH_BASE.value:
        bank = state.get("question_bank", [])                       # 取题库
        asked_count = sum(1 for q in bank if q.get("asked", False)) # 数已问过的题
        return asked_count >= min(8, len(bank))   # 至少问过 8 道题（或题库全问完）

    if stage == InterviewStage.PROJECT.value:
        projects       = state.get("resume_projects", [])           # 简历项目
        projects_asked = state.get("projects_asked", [])            # 已深挖的项目名
        if not projects:
            return True   # 无简历项目，直接推进
        return len(projects_asked) >= len(projects)  # 所有项目都深挖过

    return False                                                    # 其它阶段不在这里判断


def _next_stage(current: str) -> str:
    """返回下一个阶段的枚举值字符串（按固定顺序往后走一格）。"""
    order = [                                                       # 阶段固定顺序
        InterviewStage.WARMUP.value,
        InterviewStage.TECH_BASE.value,
        InterviewStage.PROJECT.value,
        InterviewStage.CLOSING.value,
        InterviewStage.FINISHED.value,
    ]
    try:
        idx = order.index(current)                                 # 找当前阶段位置
        return order[idx + 1] if idx + 1 < len(order) else InterviewStage.FINISHED.value  # 下一个；到尾就 FINISHED
    except ValueError:                                             # current 不在表里（异常）
        return InterviewStage.CLOSING.value                        # 兜底切到 closing


def _get_last_student_answer(messages: list) -> str:
    """提取消息列表中最后一条学员（Human）消息的文本。"""
    for msg in reversed(messages):                                 # 从后往前找
        if isinstance(msg, HumanMessage):
            return _msg_text(msg)
    return ""                                                      # 没有学员消息就返回空串

async def evaluate_answer_node(state: InterviewState) -> dict:
    """
    评估学员上一轮回答质量（EXCELLENT / ADEQUATE / WEAK / NO_ANSWER）。

    读取 state：
        total_turn_count / current_stage  判断是否跳过评估
        messages                          取学员最后一条回答
        current_question                  取当前题干（评估时作上下文）
        stage_turn_count                  用于 +1
    返回（写回 state）：
        last_answer_quality  本轮质量标签（给 generate_response 决定追问/换题）
        total_turn_count / stage_turn_count  各 +1（轮次真实发生）

    首轮和热身阶段不评估，直接返回 ADEQUATE。
    """
    total_turns   = state.get("total_turn_count", 0)    # 当前总轮数
    current_stage = state.get("current_stage", InterviewStage.WARMUP.value)  # 当前阶段
    messages      = state.get("messages", [])           # 对话消息

    # 首轮或热身阶段跳过评估：直接给中性标签 ADEQUATE，但计数照常 +1
    if total_turns == 0 or current_stage == InterviewStage.WARMUP.value:
        return {
            "last_answer_quality": AnswerQuality.ADEQUATE.value,    # 默认质量
            "total_turn_count":    total_turns + 1,                # 总轮数 +1
            "stage_turn_count":    state.get("stage_turn_count", 0) + 1,  # 阶段轮数 +1
        }
    student_answer = _get_last_student_answer(messages)   # 取学员最后一条回答文本

    # 明确未作答：空回答，或直说"不知道/不清楚/没学过" → 直接 NO_ANSWER，省一次 LLM
    if not student_answer.strip() or student_answer.strip() in ["不知道", "不清楚", "没学过"]:
        return {
            "last_answer_quality": AnswerQuality.NO_ANSWER.value,  # 标记未作答
            "total_turn_count":    total_turns + 1,               # 计数照常 +1
            "stage_turn_count":    state.get("stage_turn_count", 0) + 1,
        }

    current_q     = state.get("current_question") or {}   # 当前题（可能为空 dict）
    question_text = current_q.get("content", "上一个问题") # 题干（缺省占位）
    answer_text   = student_answer[:800]                  # 回答截断到 800 字，控制 token

    # ── 第一步：Think Tool 推理分析（不约束格式，让 LLM 写判断依据）──
    reasoning_trace = ""
    try:
        think_prompt = EVALUATE_THINK_PROMPT.format(      # 拼推理 Prompt
            question=question_text,
            answer=answer_text,
        )
        think_llm  = get_llm("qa", temperature=0)         # 取模型（温度0，评估要稳定）
        think_resp = await think_llm.ainvoke([HumanMessage(content=think_prompt)])  # 调 LLM 推理
        reasoning_trace = _msg_text(think_resp).strip()   # 取出推理文本（"内心独白"）
        logger.debug("evaluate_think.done", stage=current_stage)
    except Exception as e:                                # 推理失败不致命，继续走主评估
        logger.warning("evaluate_think.failed", error=str(e))

    # ── 第二步：主评估，把第一步的推理结论注入 prompt 末尾 ───────
    think_context = (
        f"\n\n【评估前分析】\n{reasoning_trace}" if reasoning_trace else ""  # 有推理才追加
    )
    prompt = EVALUATE_ANSWER_PROMPT.format(               # 拼主评估 Prompt
        question=question_text,
        answer=answer_text,
    ) + think_context                                     # 末尾接上推理依据

    try:
        llm = get_llm("qa", temperature=0)                # 同样温度0
        response = await llm.ainvoke([HumanMessage(content=prompt)])  # 调 LLM 打标签
        quality_str = _msg_text(response).strip().lower() # 取文本转小写，便于匹配

        # 用 in 匹配（LLM 可能带空格/换行），命中哪个就用哪个标签
        if "excellent" in quality_str or "优秀" in quality_str:
            quality = AnswerQuality.EXCELLENT.value
        elif "weak" in quality_str or "较弱" in quality_str:
            quality = AnswerQuality.WEAK.value
        elif "no_answer" in quality_str or "未作答" in quality_str:
            quality = AnswerQuality.NO_ANSWER.value
        else:
            quality = AnswerQuality.ADEQUATE.value        # 兜底：模糊情况按基本及格
    except Exception as e:                                # 评估失败 → 兜底 ADEQUATE
        logger.warning("evaluate_answer.failed", error=str(e))
        quality = AnswerQuality.ADEQUATE.value

    logger.info(
        "evaluate_answer.done",
        stage=current_stage,
        quality=quality,
        total_turns=total_turns + 1,
    )

    return {
        "last_answer_quality": quality,                   # 本轮质量标签
        "total_turn_count":    total_turns + 1,           # 总轮数 +1
        "stage_turn_count":    state.get("stage_turn_count", 0) + 1,  # 阶段轮数 +1
    }
async def generate_response_node(state: InterviewState) -> dict:
    """
    分派主函数：根据当前阶段调用对应子函数生成面试官回应。

    读取 state：
        current_stage    决定分派到哪个 _respond_*
        stage_turn_count 判断是否刚切换阶段（==1 → 插过渡语）
        messages         传给子函数 + 判断是否触发摘要
    返回（写回 state）：
        messages          面试官这一轮的回复（AIMessage）
        should_summarize  本轮是否要触发摘要压缩
        以及各 _respond_* 子函数返回的字段（如 current_question / projects_asked / followup_count）

    PROJECT/CLOSING 阶段刚切换时（stage_turns==1）插入静态过渡语；
    TECH_BASE 的过渡（介绍评价+第一题）由 _respond_tech_base 内部处理。
    """
    current_stage = state.get("current_stage", InterviewStage.WARMUP.value)  # 当前阶段
    stage_turns   = state.get("stage_turn_count", 0)    # 当前阶段轮数
    messages      = state.get("messages", [])           # 对话消息

    # 阶段刚切换时（stage_turns==1）插入静态过渡语；只 PROJECT 和 CLOSING 用
    transition_prefix = ""
    if stage_turns == 1 and current_stage not in (
        InterviewStage.WARMUP.value, InterviewStage.TECH_BASE.value
    ):
        transition_prefix = STAGE_TRANSITION_PROMPTS.get(current_stage, "")  # 取该阶段过渡语

    # 按阶段分派到对应子函数，各自返回 (回复文本, 要写回的 updates)
    if current_stage == InterviewStage.WARMUP.value:
        response_text, updates = await _respond_warmup(state)
    elif current_stage == InterviewStage.TECH_BASE.value:
        response_text, updates = await _respond_tech_base(state)
    elif current_stage == InterviewStage.PROJECT.value:
        response_text, updates = await _respond_project(state)
    elif current_stage == InterviewStage.CLOSING.value:
        response_text, updates = await _respond_closing(state)
    else:                                               # FINISHED 等：兜底文案
        response_text = "感谢您参加本次模拟面试，请稍等，正在生成评估报告..."
        updates = {}

    if transition_prefix:                               # 有过渡语就拼到回复开头
        response_text = transition_prefix + "\n\n" + response_text

    updates["messages"]         = [AIMessage(content=response_text)]  # 面试官回复（追加进 messages）
    updates["should_summarize"] = should_trigger_summary(messages)   # 判断是否该压缩摘要

    logger.info(
        "generate_response.done",
        stage=current_stage,
        response_length=len(response_text),
    )

    return updates                                      # 返回更新，写回 state


def _build_system_with_summary(state: InterviewState) -> str:
    """构建包含历史摘要的 System Prompt（有摘要就追加，让面试官"记得"早前对话）。"""
    position = state.get("target_position", "软件工程师")  # 岗位
    system   = SYSTEM_PROMPT.format(position=position)    # 渲染系统提示词
    summary  = state.get("existing_summary")              # 历史摘要（可能为空）
    if summary:                                           # 有摘要才追加
        system += f"\n\n【学员面试历史摘要】\n{summary}"
    return system


def _build_messages(
    system: str,            # System Prompt 文本
    history: list,          # 完整对话历史
    current_prompt: str,    # 本轮要让 LLM 做的"任务描述"
    window: int = 10,       # 滑动窗口大小（保留最近几条历史）
) -> list:
    """
    构建发送给 LLM 的消息列表：
        [SystemMessage] + 滑动窗口历史（排除最后一条学员消息）+ current_prompt
    """
    result = [SystemMessage(content=system)]             # 第一条永远是 System
    # history[:-1] 排除最后一条学员消息（它已在 evaluate_answer 处理过，且 current_prompt 已涵盖）
    windowed = trim_messages_to_window(history[:-1], window_size=window)  # 截取最近 window 条
    for msg in windowed:
        if not isinstance(msg, SystemMessage):           # 跳过历史里的 System（避免重复）
            result.append(msg)
    result.append(HumanMessage(content=current_prompt))  # 末尾追加本轮任务描述
    return result

async def _respond_warmup(state: InterviewState) -> tuple[str, dict]:
    """破冰热身阶段：只生成欢迎开场白，邀请自我介绍（WARMUP 仅 1 轮）。"""
    messages = state.get("messages", [])                 # 对话历史
    position = state.get("target_position", "软件工程师") # 岗位
    system   = _build_system_with_summary(state)         # 带摘要的 System Prompt

    prompt = WARMUP_PROMPT["opening"].format(position=position)  # 开场白 Prompt
    llm = get_llm("interview", temperature=0.7)          # 温度0.7，开场白更自然
    response = await llm.ainvoke(_build_messages(system, messages, prompt, window=4))  # 调 LLM
    return _msg_text(response).strip(), {}               # 返回(回复文本, 空updates)

async def _respond_tech_base(state: InterviewState) -> tuple[str, dict]:
    """
    技术基础阶段：出题或追问。

    读取 state：
        last_answer_quality  上轮质量（EXCELLENT 才追问）
        followup_count       当前题已追问次数（上限 2）
        current_question     当前题（None 表示要出新题）
        question_bank        题库（按 asked 标记选下一题）
        stage_turn_count     ==1 表示刚从 WARMUP 切来
        target_position      岗位
    返回 (回复文本, updates)，updates 可能含：
        question_bank（标记 asked）/ current_question / followup_count
    """
    messages       = state.get("messages", [])           # 对话历史
    quality        = state.get("last_answer_quality", AnswerQuality.ADEQUATE.value)  # 上轮质量
    followup_count = state.get("followup_count", 0)      # 当前题已追问次数
    current_q      = state.get("current_question")       # 当前题
    bank           = state.get("question_bank", [])      # 题库
    stage_turns    = state.get("stage_turn_count", 0)    # 本阶段轮数
    position       = state.get("target_position", "软件工程师")  # 岗位
    system         = _build_system_with_summary(state)   # 带摘要的 System Prompt
    updates: dict  = {}                                  # 收集要写回的字段

    # ── 路径1·特殊：刚从 WARMUP 推进而来（stage_turns==1，尚无 current_question）──
    if stage_turns == 1 and current_q is None:
        unasked = [q for q in bank if not q.get("asked", False)]  # 未问过的题
        if not unasked:                                  # 题库空：给一句兜底
            return ("感谢你的自我介绍！接下来我们开始技术基础环节。", {})

        first_q  = unasked[0]                            # 取第一道题
        new_bank = [                                     # 把这道题标记成 asked=True（生成新list）
            {**q, "asked": True} if q["id"] == first_q["id"] else q
            for q in bank
        ]
        updates["question_bank"]    = new_bank           # 写回更新后的题库
        updates["current_question"] = first_q            # 设为当前题
        updates["followup_count"]   = 0                  # 追问计数清零

        intro_text = _get_last_student_answer(messages)  # 学员的自我介绍文本
        prompt = INTRO_EVAL_TECH_FIRST_PROMPT.format(    # 过渡 Prompt：评价介绍 + 问第一题
            intro=intro_text[:500],
            position=position,
            first_question=first_q["content"],
        )
        llm = get_llm("interview", temperature=0.5)      # 温度0.5
        response = await llm.ainvoke(_build_messages(system, messages, prompt, window=4))
        return _msg_text(response).strip(), updates

    # ── 判断是否追问当前题：只有回答 EXCELLENT 且没追问满 2 次才追问 ──
    should_followup = (
        current_q is not None
        and followup_count < MAX_FOLLOWUP_PER_QUESTION
        and quality == AnswerQuality.EXCELLENT.value     # 技术环节门槛高：仅 EXCELLENT
    )

    if should_followup:                                  # 路径2·追问当前题
        tech_stack_str = ", ".join(current_q.get("tags", []))  # 把标签拼成技术栈串
        prompt = TECH_BASE_PROMPT["followup"].format(
            question=current_q["content"],
            answer=_get_last_student_answer(messages)[:600],
            quality=quality,
            tech_stack=tech_stack_str,
        )
        updates["followup_count"] = followup_count + 1   # 追问次数 +1
    else:                                                # 路径3·出下一道题
        unasked = [q for q in bank if not q.get("asked", False)]  # 未问过的题
        if not unasked:                                  # 题问完了 → 提示进入项目环节
            return (
                "好的，基础技术问题我们就聊到这里，接下来我想了解一下你的项目经历。",
                {"current_question": None},              # 清空当前题
            )

        next_q = unasked[0]                              # 取下一道题
        new_bank = [                                     # 标记成 asked=True
            {**q, "asked": True} if q["id"] == next_q["id"] else q
            for q in bank
        ]
        updates["question_bank"]    = new_bank           # 写回题库
        updates["current_question"] = next_q             # 设为当前题
        updates["followup_count"]   = 0                  # 追问计数清零

        prev_question = current_q["content"] if current_q else ""  # 上一题题干
        prev_answer   = _get_last_student_answer(messages)[:600]    # 上一题回答
        prompt = TECH_BASE_PROMPT["ask_with_feedback"].format(     # 反馈上题 + 问下题
            question=prev_question,
            answer=prev_answer,
            quality=quality,
            next_question=next_q["content"],
        )

    llm = get_llm("interview", temperature=0.4)          # 温度0.4
    response = await llm.ainvoke(_build_messages(system, messages, prompt, window=8))  # window=8
    return _msg_text(response).strip(), updates          # 返回(回复文本, updates)

async def _respond_project(state: InterviewState) -> tuple[str, dict]:
    """
    项目深挖阶段：基于简历项目针对性追问。

    读取 state：
        last_answer_quality  上轮质量（EXCELLENT 或 ADEQUATE 都可追问，门槛比技术环节低）
        followup_count       当前项目已追问次数（上限 2）
        resume_projects      简历项目列表
        projects_asked       已深挖过的项目名
        current_question     当前正在深挖的项目（动态构造的 dict）
        target_position      岗位
    返回 (回复文本, updates)，updates 可能含：
        followup_count / current_question / projects_asked
    """
    messages       = state.get("messages", [])           # 对话历史
    quality        = state.get("last_answer_quality", AnswerQuality.ADEQUATE.value)  # 上轮质量
    followup_count = state.get("followup_count", 0)      # 当前项目已追问次数
    projects       = state.get("resume_projects", [])    # 简历项目
    projects_asked = state.get("projects_asked", [])     # 已深挖的项目名
    current_q      = state.get("current_question")       # 当前项目（dict）
    position       = state.get("target_position", "工程师")  # 岗位
    system         = _build_system_with_summary(state)   # 带摘要的 System Prompt
    updates: dict  = {}                                  # 收集要写回的字段

    if projects:                                         # 有简历联动
        # 找下一个还没深挖过的项目
        next_project = None
        for p in projects:
            if p.get("name") not in projects_asked:
                next_project = p
                break

        # 项目环节追问门槛更低：EXCELLENT 或 ADEQUATE 均可追问
        should_followup = (
            current_q is not None
            and followup_count < MAX_FOLLOWUP_PER_QUESTION
            and quality in (AnswerQuality.EXCELLENT.value, AnswerQuality.ADEQUATE.value)
        )

        if should_followup:                              # 路径1·追问当前项目
            tech_stack_str = ", ".join(current_q.get("tech_stack", []))  # 技术栈串
            prompt = PROJECT_PROMPT["followup_with_feedback"].format(
                project_name=current_q.get("project_name", ""),
                question=current_q["content"],
                answer=_get_last_student_answer(messages)[:600],
                quality=quality,
                tech_stack=tech_stack_str,
                question_num=followup_count + 1,
            )
            updates["followup_count"] = followup_count + 1   # 追问次数 +1

        elif next_project:                               # 路径2·切到新项目，问第1问
            tech_stack_str = ", ".join(next_project.get("tech_stack", []))
            highlights_str = "\n".join(                  # 把亮点拼成多行
                f"- {h}" for h in next_project.get("highlights", [])
            ) or "（无量化数据）"

            prompt = PROJECT_PROMPT["new_project"].format(
                project_name=next_project["name"],
                project_role=next_project.get("role", "开发者"),
                tech_stack=tech_stack_str,
                highlights=highlights_str,
                description=next_project.get("description", "")[:200],
            )
            updates["current_question"] = {              # 动态构造当前项目 dict（非题库题）
                "content":      f"项目深挖：{next_project['name']}",
                "project_name": next_project["name"],
                "tech_stack":   next_project.get("tech_stack", []),
            }
            updates["projects_asked"] = projects_asked + [next_project["name"]]  # 记入已深挖
            updates["followup_count"] = 0                # 新项目追问计数清零

        else:                                            # 路径3·所有项目都深挖完 → 综合题
            prompt = PROJECT_PROMPT["synthesis"].format(position=position)

    else:                                                # 路径4·无简历联动
        # 基于学员自述出项目题（或架构设计题）
        prompt = PROJECT_PROMPT["no_resume"].format(
            position=position,
            answer=_get_last_student_answer(messages)[:400],
        )

    llm = get_llm("interview", temperature=0.5)          # 温度0.5
    response = await llm.ainvoke(_build_messages(system, messages, prompt, window=10))  # window=10
    return _msg_text(response).strip(), updates          # 返回(回复文本, updates)

async def _respond_closing(state: InterviewState) -> tuple[str, dict]:
    """
    反问收尾阶段：开放学员提问。

    读取 state：
        stage_turn_count  ≤1 发提问邀请，>1 回应学员的具体问题
        messages          取学员的提问
        target_position   回应时带上岗位上下文
    返回 (回复文本, {})——本阶段不更新状态字段。
    """
    messages    = state.get("messages", [])              # 对话历史
    stage_turns = state.get("stage_turn_count", 0)       # 本阶段轮数
    position    = state.get("target_position", "工程师")  # 岗位
    system      = _build_system_with_summary(state)      # 带摘要的 System Prompt

    if stage_turns <= 1:                                 # 刚进 CLOSING：邀请学员提问
        prompt = CLOSING_PROMPT["opening"]
    else:                                                # 学员已提问：回应其问题
        last_answer = _get_last_student_answer(messages) # 学员的提问
        prompt = CLOSING_PROMPT["respond_question"].format(
            question=last_answer[:300],
            position=position,
            job_description=state.get("job_description", ""),
        )

    llm = get_llm("interview", temperature=0.6)          # 温度0.6，回应更灵活
    response = await llm.ainvoke(_build_messages(system, messages, prompt, window=4))  # window=4
    return _msg_text(response).strip(), {}               # 返回(回复文本, 空updates)
async def generate_report_node(state: InterviewState) -> dict:
    """
    基于完整对话记录和历史摘要，生成五维度结构化评估报告。

    读取 state：
        messages           完整对话（取最近 40 条格式化）
        existing_summary   历史摘要（早期对话压缩，作全局视角）
        target_position    岗位
        total_turn_count   总轮数（写进 Prompt 和 structured_output）
        session_id         写进 structured_output
    返回（写回 state）：
        report             五维度报告 dict（给 save_report 存库）
        messages           面试结束通知（AIMessage）
        structured_output  给 API 层直接读的摘要数据
    """
    messages  = state.get("messages", [])                # 完整对话
    summary   = state.get("existing_summary", "")        # 历史摘要
    position  = state.get("target_position", "软件工程师") # 岗位

    # 取最近 40 条对话，只保留学员/面试官消息，格式化成文本
    recent_messages = [
        msg for msg in messages[-40:]                    # 最近 40 条
        if isinstance(msg, (HumanMessage, AIMessage))    # 只要人/AI 消息
    ]
    conversation_text = "\n".join([                      # 拼成"角色：内容"多行文本
        f"{'面试官' if isinstance(m, AIMessage) else '学员'}："
        f"{_msg_text(m)}"
        for m in recent_messages
    ])

    prompt = GENERATE_REPORT_PROMPT.format(              # 拼报告 Prompt（双层输入）
        position=position,
        job_description=state.get("job_description", ""),
        job_requirements=json.dumps(state.get("job_requirements") or {}, ensure_ascii=False),
        history_summary=summary or "（无历史摘要）",      # 早期对话用摘要
        conversation=conversation_text[:4000],          # 近期对话截断到 4000 字
        total_turns=state.get("total_turn_count", 0),
    )

    structured_llm = get_structured_llm("interview", InterviewReport)

    report_dict = None
    for attempt in range(2):                             # function_calling 偶发返回 None → 判空+重试
        try:
            result: InterviewReport = await structured_llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT.format(position=position)),
                HumanMessage(content=prompt),
            ])
            if result is None:
                raise ValueError("structured output returned None")
            report_dict = result.model_dump()
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("generate_report.retry", error=str(e))
                await asyncio.sleep(1)
            else:
                logger.warning("generate_report.failed", error=str(e))

    if report_dict is None:                              # 两次均失败 → 硬编码保守报告
        report_dict = {
            "dimensions":         [],
            "overall_score":      60,                    # 保守默认分，提示报告生成有误
            "strengths":          ["参与了完整的模拟面试流程"],
            "improvements":       ["建议深化技术基础知识", "加强项目经历的量化描述"],
            "overall_comment":    "感谢参加本次模拟面试，请参考各维度建议持续提升。",
            "recommended_topics": [],
            "next_step_advice":   "建议针对薄弱点系统复习，1-2周后再次模拟面试。",
            "evidence":           [],
            "risk_flags":         ["自动报告生成失败，需要招聘人员人工复核"],
        }

    overall_score = report_dict.get("overall_score", 0)
    closing_message = "好的，本次 AI 初面已经结束，感谢你的参与。结果将由 HR 后续通知。"

    logger.info(
        "generate_report.done",
        overall_score=overall_score,
        dimensions=len(report_dict.get("dimensions", [])),
    )

    return {
        "report":   report_dict,                          # 给 save_report 存库
        "messages": [AIMessage(content=closing_message)], # 结束通知（追加进 messages）
        "structured_output": {                            # 给 API 层直接读的摘要
            "session_id":  state["session_id"],
            "report":      report_dict,
            "total_turns": state.get("total_turn_count", 0),
        },
    }
async def save_report_node(state: InterviewState) -> dict:
    """
    将 InterviewReport 写入 interview_sessions，更新 status=finished。

    读取 state：
        student_id / session_id  拼 thread_id 定位会话行
        report                   7.9 生成的报告 dict
    返回：{}（不改 state，只落库）
    """
    thread_id = build_thread_id(state["student_id"], state["session_id"])  # 定位会话的 key
    report    = state.get("report", {})                  # 取报告 dict

    async with AsyncSessionLocal() as session:           # 开异步 DB 会话
        try:
            result = await session.execute(                       # UPDATE（行已在 POST /sessions 插过）
                text("""
                    UPDATE interview_sessions
                    SET report        = :report,
                        overall_score = :overall_score,
                        status        = 'finished',
                        finished_at   = NOW(),
                        updated_at    = NOW()
                    WHERE thread_id = :thread_id
                      AND tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND status = 'in_progress'
                """),
                {
                    "report":        json.dumps(report, ensure_ascii=False),  # dict→JSON 字符串（中文不转义）
                    "overall_score": report.get("overall_score", 0),          # 综合分单独存一列
                    "thread_id":     thread_id,
                    "tenant_id": state["tenant_id"],
                    "student_id": state["student_id"],
                },
            )
            if result.rowcount != 1:
                raise RuntimeError("面试会话不存在、归属不匹配或状态已变化")
            await ApplicationService(session).complete_interview(
                application_id=state["application_id"],
                tenant_id=state["tenant_id"],
                session_id=state["session_id"],
            )
            await session.commit()                       # 提交
            logger.info("save_report.done", thread_id=thread_id)
        except Exception as e:
            await session.rollback()
            logger.error("save_report.db_failed", error=str(e))
            raise

    return {}

async def save_memory_node(state: InterviewState) -> dict:
    """
    按需触发摘要压缩，将最新摘要 UPSERT 到 interview_sessions。

    读取 state：
        messages / student_id / session_id / tenant_id / target_position / resume_review_id
        existing_summary   现有摘要（压缩时与之合并）
        should_summarize   是否触发压缩
    返回：{}（不改 state，只落库）

    ON CONFLICT (thread_id) 需要表上有 UNIQUE(thread_id) 约束。
    """
    messages   = state.get("messages", [])               # 对话历史
    student_id = state["student_id"]                     # 学员 ID
    session_id = state["session_id"]                     # 会话 ID
    tenant_id  = state["tenant_id"]                      # 租户 ID
    thread_id  = build_thread_id(student_id, session_id) # 定位 key
    summary    = state.get("existing_summary")           # 现有摘要

    # 按需压缩：should_summarize 为 True 时调 LLM 把对话压成新摘要
    if state.get("should_summarize", False):
        try:
            summary = await compress_to_summary(messages, summary)  # 与旧摘要合并压缩
        except Exception as e:
            logger.warning("save_memory.compress_failed", error=str(e))

    async with AsyncSessionLocal() as session:           # 开异步 DB 会话
        try:
            await session.execute(                       # UPSERT：存在则更新摘要，不存在则插入
                text("""
                    INSERT INTO interview_sessions
                        (id, tenant_id, student_id, session_id, thread_id,
                         target_position, resume_review_id, summary, status)
                    VALUES
                        (:id, :tenant_id, :student_id, :session_id, :thread_id,
                         :target_position, :resume_review_id, :summary, 'in_progress')
                    ON CONFLICT (thread_id) DO UPDATE
                        SET summary    = EXCLUDED.summary,
                            updated_at = NOW()
                """),
                {
                    "id":               str(uuid.uuid4()),              # 插入时的新主键
                    "tenant_id":        tenant_id,
                    "student_id":       student_id,
                    "session_id":       session_id,
                    "thread_id":        thread_id,
                    "target_position":  state.get("target_position", ""),
                    "resume_review_id": state.get("resume_review_id"),
                    "summary":          summary,                        # 最新摘要
                },
            )
            await session.commit()                       # 提交
        except Exception as e:                           # 失败回滚 + 记日志
            await session.rollback()
            logger.warning("save_memory.db_failed", error=str(e))

    return {}
