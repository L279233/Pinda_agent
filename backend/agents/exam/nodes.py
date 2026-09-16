# backend/agents/exam/nodes.py
import asyncio
import json
import uuid
from typing import Any
import httpx
from sqlalchemy import text
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from backend.agents.exam.state import (
    ExamState,
    SubjectiveReviewResult,
    WeakPointsReport,
)
from backend.agents.exam.prompts import (
    SYSTEM_PROMPT,
    SUBJECTIVE_REVIEW_PROMPT,
    SUBJECTIVE_THINK_PROMPT,
    CODE_REVIEW_PROMPT,
    WEAK_POINTS_ANALYSIS_PROMPT,
)
from backend.core.llm_factory import get_llm, get_structured_llm
from backend.core.logger import get_logger
from backend.core.application_service import ApplicationService
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def _get_message_content(msg) -> str:
    """统一获取消息文本内容（兼容 text 属性和 content 属性）"""
    if hasattr(msg, "text") and not callable(getattr(msg, "text", None)):
        return msg.text
    if isinstance(msg.content, str):
        return msg.content
    return str(msg.content)


def _chinese_to_int(s: str) -> int:
    """中文数字转整数，转换失败时直接 int()，仍失败时返回1"""
    cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    try:
        return int(s)
    except ValueError:
        return cn_map.get(s, 1)


# ──────────────────────────────────────────────────────────────
# 节点1：parse_word — 解析学员作答 Word 文件
# ──────────────────────────────────────────────────────────────

def _sync_parse_word(word_path: str) -> list:
    """
    同步解析 Word 文件（在线程池中运行，避免阻塞事件循环）。

    返回 list[dict]，每个 dict 包含：
        question_no:    题号（int）
        header_text:    原始题目行文本
        student_answer: 学员作答文本（代码题为代码字符串）
        is_code:        True 表示代码题（仅代码题有此字段）

    ── 整体思路（强烈建议先读这段，再看下面的代码）─────────────
    这个函数像一台「状态机」：把 Word 一段一段（paragraph）往下扫，
    边扫边把内容归到「当前这道题」名下。靠 4 个状态变量记住扫到哪儿了：

        current_question     当前正在收集的题（dict）；None=还没遇到第一题
        current_answer_lines 当前题「普通答案」的行，逐行累积
        in_code_block        当前是否正处在代码块内部（True/False）
        code_buffer          当前代码块内的行，逐行累积

    每读到一段，先判断它是哪种「角色」，再据此更新状态：
        ① 空行              → 代码块内当作代码的一部分保留；块外忽略
        ② 题头(第X题/Q.X)   → 先把上一题「结算」存好，再开一道新题、清空状态
        ③ 代码围栏(三反引号) → 翻转 in_code_block；闭合时把 code_buffer 存为代码答案
        ④ 代码块内的行      → 原样塞进 code_buffer（不 strip，保留缩进）
        ⑤ 其他行            → 属于当前题的答案：跳过模板提示行、处理「答：」前缀

    「结算」= 遇到下一题题头、或文件读完时，把累积的答案 join 成 student_answer。
    ───────────────────────────────────────────────────────────
    """
    from docx import Document                     # python-docx：用来读 .docx
    import re                                      # 正则：识别题头、提取题号

    doc = Document(word_path)                      # 打开 Word 文档
    parsed_questions = []                          # 最终结果：所有题组成的列表
    current_question = None                        # 当前正在收集的题；None=还没遇到第一题
    current_answer_lines = []                      # 当前题「普通答案」累积的行
    in_code_block = False                          # 是否正处在代码块内部
    code_buffer = []                               # 当前代码块累积的行

    for para in doc.paragraphs:                    # 逐段扫描整个文档（核心循环）
        para_text = para.text.strip()              # 该段文本（去掉首尾空白）
        # print(f'para_text: {para_text}')
        # ① 角色：空行
        if not para_text:                          # 这一段是空行
            if in_code_block:                      #   若正处在代码块内
                code_buffer.append("")             #     空行也算代码的一部分，保留（维持代码格式）
            continue                               #   块外的空行直接跳过，处理下一段

        # 先用正则判断这一段是不是「题目开头行」，支持 第X题 / Q.X / 题目X 三种格式
        is_question_header = re.match(
            r"^(第?\s*[一二三四五六七八九十\d]+\s*[题、。.]|Q\.?\s*\d+|题目\s*\d+)",
            para_text,
            re.IGNORECASE,
        )
        # print(f'is_question_header: {is_question_header}')
        # ② 角色：题头行 → 开一道新题
        if is_question_header:
            # print(f'1current_question: {current_question}')
            # 先把「上一题」结算并放进结果（如果之前已经在收集某道题）
            if current_question is not None:
                if not current_question.get("is_code"):        # 代码题已在闭合围栏时存过答案，这里只结算非代码题
                    answer_text = "\n".join(code_buffer) if in_code_block \
                        else "\n".join(current_answer_lines)   # 普通题：把累积的答案行用换行 join 起来
                    current_question["student_answer"] = answer_text.strip()
                parsed_questions.append(current_question)       # 上一题收尾，加入结果列表
            # print(f'2current_question: {current_question}')
            # print(f'parsed_questions: {parsed_questions}')
            # 从题头里提取题号：优先阿拉伯数字，其次中文数字（如「三」）
            match = re.search(r"[一二三四五六七八九十\d]+", para_text)
            # print(f'match: {match}')
            q_no = _chinese_to_int(match.group()) if match else len(parsed_questions) + 1
            # print(f'q_no: {q_no}')
            # 开一道新题，并把所有状态清零（开始为新题收集内容）
            current_question = {"question_no": q_no, "header_text": para_text, "student_answer": ""}
            current_answer_lines = []              # 清空普通答案累积
            code_buffer = []                       # 清空代码累积
            in_code_block = False                  # 退出可能残留的代码块状态

        # ③ 角色：代码围栏行（以三反引号开头）→ 进入 / 退出代码块
        elif para_text.startswith("```"):
            # print(f'in_code_block: {in_code_block}')
            in_code_block = not in_code_block       # 翻转开关：第一个围栏=进入，第二个围栏=退出
            # print(f"3current_question-->{current_question}")
            if not in_code_block and current_question:   # 刚「退出」（即闭合）时，立刻结算代码答案
                current_question["student_answer"] = "\n".join(code_buffer).strip()
                current_question["is_code"] = True       # 打上代码题标记（之后结算时据此跳过覆盖）

        # ④ 角色：代码块内部的普通行
        elif in_code_block:
            # print(f'in_code_block: {in_code_block}')
            code_buffer.append(para.text)           # 注意用 para.text（未 strip 的原文）→ 保留缩进！
            # print(f'code_buffer: {code_buffer}')
        # ⑤ 角色：其他行 → 都算「当前题」的答案内容
        elif current_question is not None:           # 前提：已经进入某道题（题头之后）才会收答案
            # 跳过纯模板提示行（这些是模板自带的，不是学员真正写的答案）
            # print(f'4current_question: {current_question}')
            skip_prefixes = ["作答区", "请在此处"]
            if any(para_text.startswith(p) for p in skip_prefixes):
                pass                                # 命中模板行 → 什么都不做，跳过
            else:
                # 处理「答：X」前缀：只取冒号后面的内容，避免把「答：」二字也算进答案
                answer_prefixes = ["答：", "答:", "Answer:"]
                extracted = None
                for prefix in answer_prefixes:
                    if para_text.startswith(prefix):            # 这一行以「答：」开头
                        rest = para_text[len(prefix):].strip()  # 取前缀后面的内容
                        if rest:                                # 冒号后确实有内容（如「答：A」）
                            extracted = rest                    #   提取出来（结果就是「A」）
                        break                                   # 不管冒号后有没有内容，都不再把整行加入
                if extracted is not None:                       # 「答：X」这种行内带答案 → 收下提取结果
                    current_answer_lines.append(extracted)
                elif not any(para_text.startswith(p) for p in answer_prefixes):
                    current_answer_lines.append(para_text)      # 普通答案行（不以「答：」开头）→ 整行收下

                # print(f'current_answer_lines: {current_answer_lines}')
                # 说明：若是「答：」空冒号行，上面 break 后 extracted 仍为 None、又不满足 elif（它以答：开头），
                #       于是这一行被「跳过」——真正的答案在它下面几行，会走本分支被收进 current_answer_lines。
        # print(f'parsed_questions-->{parsed_questions}')
        # print("*"*80)
    # 循环结束后，单独把「最后一题」结算（它后面没有题头来触发结算）
    if current_question is not None:
        # print(f'5current_question: {current_question}')
        if not current_question.get("is_code"):
            answer_text = "\n".join(code_buffer) if in_code_block \
                else "\n".join(current_answer_lines)
            current_question["student_answer"] = answer_text.strip()
        parsed_questions.append(current_question)

    return parsed_questions                          # 返回所有题的解析结果


async def parse_word_node(state: ExamState) -> dict:
    """
    解析学员提交的 Word 试卷文件，提取各题作答内容。

    python-docx 内部有文件 I/O（打开 .docx zip）和 XML 解析（ElementTree），
    两者都是同步阻塞操作，不能直接在 async 函数里调用。
    用 run_in_executor(None, ...) 放入默认线程池，asyncio 事件循环继续处理
    其他协程，线程完成后 await 恢复。
    """
    if state.get("submission_source") == "online":
        logger.info(
            "parse_online.done",
            questions_found=len(state.get("parsed_questions", [])),
        )
        return {"parsed_questions": state.get("parsed_questions", [])}

    word_path = state["word_file_path"]

    try:
        loop = asyncio.get_running_loop()
        parsed_questions = await loop.run_in_executor(None, _sync_parse_word, word_path)

        logger.info(
            "parse_word.done",
            file=word_path,
            questions_found=len(parsed_questions),
        )

        return {"parsed_questions": parsed_questions}

    except Exception as e:
        logger.error("parse_word.failed", error=str(e), file=word_path)
        # 优雅降级：文件损坏或格式不符时，返回空列表。
        # 后续 load_questions_meta_node 从 DB 补全题目信息，
        # student_answer 全部为空字符串，教师人工补批。
        return {"parsed_questions": []}

# backend/agents/exam/nodes.py（接 6.3）

# ──────────────────────────────────────────────────────────────
# 节点2：load_questions_meta — 加载试卷题目元数据
# ──────────────────────────────────────────────────────────────

async def load_questions_meta_node(state: ExamState) -> dict:
    """
    从数据库加载试卷的完整题目元数据（含标准答案、得分点、知识点标签），
    与解析出的学员答案合并，覆盖写入 parsed_questions。
    """
    exam_id = state["exam_id"]
    parsed  = state["parsed_questions"]   # parse_word_node 的输出

    async with AsyncSessionLocal() as session:
        # ── ① 加载题目列表 ──────────────────────────────────────
        result = await session.execute(
            text("""
                SELECT id, question_no, question_type, content,
                       correct_answer, score, knowledge_tag
                FROM questions
                WHERE exam_id = :exam_id
                ORDER BY question_no
            """),
            {"exam_id": exam_id},
        )
        # print(f'result1111: {result}')
        questions = result.mappings().all()
        # print(f'questions: {questions}')
        # print(f'questions: {len(questions)}')
        # ── ② 加载得分点（仅简答题有）──────────────────────────
        question_ids = [str(q["id"]) for q in questions]
        scoring_points_rows = []
        if question_ids:
            # 动态构造 IN 子句（避免 asyncpg 的 ANY(:qids::uuid[]) 类型不兼容问题）
            param_names = [f":qid_{i}" for i in range(len(question_ids))]
            # print(f'param_names: {param_names}')
            qid_params  = {f"qid_{i}": qid for i, qid in enumerate(question_ids)}
            # print(f'qid_params: {qid_params}')

            sp_result = await session.execute(
                text(f"""
                    SELECT id, question_id, point_desc, point_score
                    FROM scoring_points
                    WHERE question_id IN ({", ".join(param_names)})
                      AND is_active = TRUE
                    ORDER BY question_id, id
                """),
                qid_params,
            )
            scoring_points_rows = sp_result.mappings().all()
        # print(f'scoring_points_rows: {scoring_points_rows}')


    # ── ③ 按 question_id 聚合得分点 ─────────────────────────────
    sp_by_question: dict[str, list] = {}
    for sp in scoring_points_rows:
        qid = str(sp["question_id"])
        #if qid not in sp_by_question:sp_by_question[qid] = []sp_by_question[qid].append({...})
        sp_by_question.setdefault(qid, []).append({
            "id":    str(sp["id"]),
            "desc":  sp["point_desc"],
            "score": sp["point_score"],
        })
    # print(f'sp_by_question: {sp_by_question}')

    # ── ④ 以 DB 题目为主，合并解析结果 ─────────────────────────
    parsed_by_no = {p["question_no"]: p for p in parsed}
    merged_questions = []

    for q in questions:
        q_no = q["question_no"]
        merged_questions.append({
            "question_id":    str(q["id"]),
            "question_no":    q_no,
            "question_type":  q["question_type"],
            "content":        q["content"],
            "student_answer": parsed_by_no.get(q_no, {}).get("student_answer", ""),
            "correct_answer": q["correct_answer"] or "",
            "scoring_points": sp_by_question.get(str(q["id"]), []),
            "full_score":     q["score"],
            "knowledge_tag":  q["knowledge_tag"] or "",
        })

    logger.info(
        "load_questions_meta.done",
        exam_id=exam_id,
        total_questions=len(merged_questions),
    )

    return {"parsed_questions": merged_questions}

# ── 第一轨：规则引擎（客观题）────────────────────────────────


def _normalize_answer(answer: str) -> str:
    """
    标准化答案字符串，消除大小写/空格/标点差异，使多选题选项顺序无关。

    处理步骤：
        1. 统一大写（A/a → A）
        2. 去除所有空格、中文逗号、英文逗号
           "A, B, C" → "ABC"，"A，B，C" → "ABC"
        3. 字符排序（多选题 "BA" 和 "AB" 视为等价）
           sorted("ABC") → ['A','B','C'] → "ABC"
    """
    cleaned = answer.upper().replace(" ", "").replace("，", "").replace(",", "")
    return "".join(sorted(cleaned))


async def _run_objective_track(questions: list[dict]) -> list[dict]:
    """
    客观题规则批改。虽然声明为 async，内部没有 await，
    但保持 async 统一接口方便在 asyncio.gather 中与其他两轨并行。
    """
    results = []
    for q in questions:
        student_ans = _normalize_answer(q["student_answer"])
        correct_ans = _normalize_answer(q["correct_answer"])
        is_correct  = (student_ans == correct_ans)

        results.append({
            "question_id":    q["question_id"],
            "question_no":    q["question_no"],
            "question_type":  q["question_type"],
            "knowledge_tag":  q.get("knowledge_tag", ""),
            "content":        q.get("content", ""),
            "student_answer": q["student_answer"],
            "correct_answer": q["correct_answer"],
            "is_correct":     is_correct,
            "score":          q["full_score"] if is_correct else 0,
            "full_score":     q["full_score"],
            "needs_review":   False,          # 客观题不需要教师复核
            "ai_feedback":    "正确" if is_correct else f"正确答案：{q['correct_answer']}",
        })

    return results


# backend/agents/exam/nodes.py（接 6.5）

# ── 第二轨：LLM 语义评分（简答题）────────────────────────────

async def _review_one_subjective(q: dict) -> dict:
    """批改单道简答题，两步流程：先 Think Tool 推理，再结构化评分。"""
    # 构造得分点描述文本
    scoring_points_text = "\n".join([
        f"  {i + 1}. [{sp['score']}分] {sp['desc']}"
        for i, sp in enumerate(q["scoring_points"])
    ]) or "  （无预设得分点，请综合评分）"
    # print(f' scoring_points_text: {scoring_points_text}')
    student_answer_text = q["student_answer"] or "（学员未作答）"

    # ── 第一步：Think Tool 推理分析 ───────────────────────────
    reasoning_trace = ""
    try:
        think_prompt = SUBJECTIVE_THINK_PROMPT.format(
            question_content=q["content"],
            scoring_points=scoring_points_text,
            student_answer=student_answer_text,
        )
        think_llm  = get_llm("exam_subjective", temperature=0)
        think_resp = await think_llm.ainvoke([HumanMessage(content=think_prompt)])
        reasoning_trace = _get_message_content(think_resp).strip()
        logger.debug("subjective_think.done", question_no=q.get("question_no"))
    except Exception as e:
        # 推理失败不影响主评分，降级为直接评分
        logger.warning("subjective_think.failed", error=str(e))
    # print(f' reasoning_trace: {reasoning_trace}')
    # ── 第二步：结构化评分（附带推理结论）────────────────────
    think_context = (
        f"\n\n【批改前分析】\n{reasoning_trace}" if reasoning_trace else ""
    )
    review_prompt = SUBJECTIVE_REVIEW_PROMPT.format(
        question_content=q["content"],
        scoring_points=scoring_points_text,
        full_score=q["full_score"],
        student_answer=student_answer_text,
    ) + think_context

    structured_llm = get_structured_llm("exam_subjective", SubjectiveReviewResult)
    result: SubjectiveReviewResult = await structured_llm.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=review_prompt),
    ])
    return {
        "question_id":    q["question_id"],
        "question_no":    q["question_no"],
        "question_type":  "short_answer",
        "knowledge_tag":  q.get("knowledge_tag", ""),
        "content":        q.get("content", ""),
        "student_answer": q["student_answer"],
        "score":          result.total_score,
        "full_score":     result.full_score,
        "needs_review":   result.confidence < 0.7,   # 低把握度标记教师复核
        "confidence":     result.confidence,
        "ai_feedback":    result.overall_comment,
        "point_results":  [p.model_dump() for p in result.point_results],
    }

async def _run_subjective_track(questions: list[dict]) -> list[dict]:
    """
    简答题批改，每 3 题一组并行处理。

    并行策略：
        把 N 道简答题切成 ⌈N/3⌉ 个组，组内 asyncio.gather 并行，
        组间顺序执行。目的是避免同时发起几十个 LLM 请求导致 API 限速。
    """
    if not questions:
        return []

    GROUP_SIZE = 3
    groups = [questions[i:i + GROUP_SIZE] for i in range(0, len(questions), GROUP_SIZE)]
    all_results = []
    for group in groups:
        group_results = await asyncio.gather(
            *[_review_one_subjective(q) for q in group],
            return_exceptions=True,
        )
        for q, result in zip(group, group_results):
            if isinstance(result, Exception):
                # 单题失败降级：标记 needs_review=True，不阻断整批
                logger.warning(
                    "subjective_track.question_failed",
                    question_id=q["question_id"],
                    error=str(result),
                )
                all_results.append({
                    "question_id":    q["question_id"],
                    "question_no":    q["question_no"],
                    "question_type":  "short_answer",
                    "knowledge_tag":  q.get("knowledge_tag", ""),
                    "content":        q.get("content", ""),
                    "student_answer": q["student_answer"],
                    "score":          0,
                    "full_score":     q["full_score"],
                    "needs_review":   True,
                    "confidence":     0.0,
                    "ai_feedback":    "AI 评分失败，已标记需招聘官人工复核",
                    "point_results":  [],
                })
            else:
                all_results.append(result)

    return all_results

# backend/agents/exam/nodes.py（接 6.6）

# ── 第三轨：LLM（代码题）────────────────────────────
# backend/agents/exam/nodes.py（接 6.6）

async def _run_code_track(questions: list[dict]) -> list[dict]:
    """
    代码题批改入口：顺序逐题批改（代码题一般数量少，不必并行）。

    参数 questions：本卷里所有「代码题」的合并题目字典列表（来自 6.4 的合并结果，
                    已按题型筛选，这里只会收到 question_type == "code" 的题）。
    返回：每道代码题的批改结果字典组成的列表。
    """
    if not questions:                       # 没有代码题（空列表）
        return []                           # 直接返回空，省去后续循环
    results = []                            # 收集每道题的批改结果
    for q in questions:                     # 逐道代码题处理
        results.append(await _review_one_code(q))   # 调用单题批改，await 等它出结果
    return results                          # 返回全部代码题的批改结果


async def _review_one_code(q: dict) -> dict:
    """
    批改「单道代码题」：交给大模型综合评估功能正确性 + 代码质量。

    参数 q：一道代码题的「合并题目字典」（就是 6.4 load_questions_meta_node 产出的那种），
            本函数会读取它的下列键：
        q["question_id"]     题目 ID（str），原样写进批改结果，方便回填数据库
        q["question_no"]     题号（int）
        q["content"]         题目内容（str），喂给 LLM 当评分依据
        q["student_answer"]  学员提交的代码（str）
        q["correct_answer"]  标准答案（str）：这道题的「满分参考实现」，给 LLM 当对照标杆
        q["full_score"]      该题满分（int）
        q["knowledge_tag"]   知识点标签（str，可能缺省，用 .get 兜底）
    返回：一道代码题的批改结果字典（含 score / confidence / needs_review / quality_feedback 等）。
    """
    student_code       = q["student_answer"]                # 取出学员代码
    reference_solution = q.get("correct_answer", "") or ""  # 取出参考实现（缺省给空串）

    # 交给大模型评分：返回（逐条评语, 得分, 把握度）三元组
    feedback, score, confidence = await _llm_code_review(
        question_content=q["content"],          # 题目内容
        student_code=student_code,              # 学员代码
        full_score=q["full_score"],             # 该题满分（评分上限）
        reference_solution=reference_solution,  # 参考实现（对照标杆）
    )
    # ── 组装批改结果字典返回 ───────────────────────────────────
    return {
        "question_id":      q["question_id"],           # 题目 ID
        "question_no":      q["question_no"],           # 题号
        "question_type":    "code",                     # 题型固定 code
        "knowledge_tag":    q.get("knowledge_tag", ""), # 知识点（缺省空串）
        "content":          q.get("content", ""),       # 题目内容（缺省空串）
        "student_answer":   student_code,               # 学员代码
        "score":            score,                      # 本题最终得分（0~full_score）
        "full_score":       q["full_score"],            # 本题满分
        "confidence":       confidence,                 # LLM 评分把握度（0~1）
        "needs_review":     confidence < 0.7,           # 把握度低于 0.7 → 标记教师复核
        "quality_feedback": feedback,                   # LLM 逐条评语（list[str]，下游会用到）
        "ai_feedback":      "\n".join(feedback),        # 评语拼成单个字符串，方便展示
    }


async def _llm_code_review(
    question_content:   str,        # 题目内容
    student_code:       str,        # 学员代码
    full_score:         int,        # 该题满分（评分上限）
    reference_solution: str = "",   # 参考实现（默认空串）
) -> tuple[list[str], int, float]:
    """
    大模型综合评估代码：功能正确性 + 代码质量。

    返回一个三元组 (feedback, score, confidence)：
        feedback：  list[str]，逐条评语（功能正确性 + 四个质量维度）
        score：     int，得分，范围 0 ~ full_score
        confidence：float，LLM 自报的评分把握度，范围 0 ~ 1（越不确定越低）
    """
    # 用模板拼出完整 Prompt（把题目 / 参考实现 / 学员代码 / 满分填进去）
    prompt = CODE_REVIEW_PROMPT.format(
        question=question_content,                                  # {question}
        reference_solution=reference_solution or "（无参考实现，仅按代码本身评估）",  # {reference_solution}，没有就填兜底语
        code=student_code or "（未提交代码）",                       # {code}，没提交就填兜底语
        full_score=full_score,                                      # {full_score}：满分制
    )

    llm      = get_llm("exam_code")             # 从 llm_factory 取「代码评估」用的模型实例
    response = await llm.ainvoke([              # 异步调用大模型
        SystemMessage(content=SYSTEM_PROMPT),   # 系统提示（统一人设/规则）
        HumanMessage(content=prompt),           # 把上面拼好的 Prompt 作为用户消息
    ])

    # 取出回复纯文本，并去掉模型可能多带的 ```json 代码围栏，方便 json.loads
    raw = _get_message_content(response).strip().replace("```json", "").replace("```", "").strip()
    try:                                        # 解析可能失败，包 try
        data       = json.loads(raw)            # 把回复解析成字典
        feedback   = data.get("feedback", [])   # 取逐条评语列表（缺省空列表）
        score      = min(int(data.get("score", 0)), full_score)  # 取分数，封顶到满分
        confidence = float(data.get("confidence", 0))            # 取把握度（缺省 0）
    except Exception:                           # 模型没返回合法 JSON
        # JSON 解析失败时的降级：不崩，给 0 分 + 把握度 0（→ 必复核）+ 提示人工
        feedback   = ["代码评估结果解析失败，请招聘官人工复核"]
        score      = 0
        confidence = 0.0

    return feedback, score, confidence          # 返回（评语列表, 得分, 把握度）

# ──────────────────────────────────────────────────────────────
# 节点3：run_three_tracks — 三轨并行批改
# ──────────────────────────────────────────────────────────────

async def run_three_tracks_node(state: ExamState) -> dict:
    """
    双轨并行批改节点：客观题规则轨 + 主观/代码题 AI 评估轨。

    读取 state：
        state["parsed_questions"]  6.4 合并好的完整题目列表（含 question_type）
    返回（会被 LangGraph 合并进 state）：
        objective_results / subjective_results / code_results  各题型批改结果列表

    两轨：
        第一轨：规则引擎（客观题：单选/多选/判断）
        第二轨：LLM 评估（简答题与代码题在轨内并发）

    asyncio.gather(return_exceptions=True)：
        某一轨抛异常不会中断其他轨，异常作为返回值处理；失败的轨结果置为空列表。
    """
    questions = state["parsed_questions"]  # 取出合并好的题目列表

    # 按题型分组；简答题和代码题属于同一条 AI 评估轨
    objective_qs = [q for q in questions if q["question_type"] in  # 客观题：单选/多选/判断
                    ("single_choice", "multi_choice", "judge")]
    subjective_qs = [q for q in questions if q["question_type"] == "short_answer"]  # 简答题
    code_qs = [q for q in questions if q["question_type"] == "code"]  # 代码题

    logger.info(  # 记一条日志：三轨各有多少题
        "three_tracks.start",
        objective=len(objective_qs),
        subjective=len(subjective_qs),
        code=len(code_qs),
    )

    async def run_ai_track() -> tuple[list[dict], list[dict]]:
        return await asyncio.gather(
            _run_subjective_track(subjective_qs),
            _run_code_track(code_qs),
            return_exceptions=False,
        )

    # 双轨并行启动，任一轨失败不影响另一轨
    raw = await asyncio.gather(
        _run_objective_track(objective_qs),
        run_ai_track(),
        return_exceptions=True,  # 某轨抛异常→该位置返回异常对象而非崩溃
    )

    # 逐轨取结果：是异常就退化成空列表，正常就用返回值
    objective_results = raw[0] if not isinstance(raw[0], Exception) else []
    subjective_results, code_results = raw[1] if not isinstance(raw[1], Exception) else ([], [])

    # 把失败的轨记到错误日志（不影响其余两轨的正常流程）
    for name, exc in zip(["objective", "ai"], raw):  # 轨名与返回值配对
        if isinstance(exc, Exception):  # 这一轨失败了
            logger.error(f"two_tracks.{name}_failed", error=str(exc))

    logger.info(  # 记一条日志：三轨各完成多少题
        "two_tracks.done",
        objective_done=len(objective_results),
        subjective_done=len(subjective_results),
        code_done=len(code_results),
    )

    return {  # 返回三轨结果，写回 state
        "objective_results": objective_results,
        "subjective_results": subjective_results,
        "code_results": code_results,
    }

# ──────────────────────────────────────────────────────────────
# 节点4：aggregate_results — 汇总预批改结果
# ──────────────────────────────────────────────────────────────

async def aggregate_results_node(state: ExamState) -> dict:
    """
    汇总节点：合并三轨结果，按题号排序，计算总分和需复核题数。

    读取 state：
        state["objective_results"] / ["subjective_results"] / ["code_results"]  三轨结果
    返回：
        pre_review_summary  一个汇总字典（总分/满分/得分率/需复核数/逐题列表），
                            它是后续 HitL（6.9）展示给教师的核心数据结构。
    """
    all_results = (                                     # 三轨结果拼成一个大列表
        state.get("objective_results", [])             # 客观题结果（缺省空列表）
        + state.get("subjective_results", [])          # 简答题结果
        + state.get("code_results", [])                # 代码题结果
    )
    all_results.sort(key=lambda x: x.get("question_no", 0))  # 按题号升序排（三轨混在一起，重排成卷面顺序）
    total_score        = sum(r.get("score", 0) for r in all_results)       # 累加每题得分 = 总分
    full_score         = sum(r.get("full_score", 0) for r in all_results)  # 累加每题满分 = 总满分
    score_rate         = round(total_score / full_score, 4) if full_score > 0 else 0.0  # 得分率（防除零）
    needs_review_count = sum(1 for r in all_results if r.get("needs_review", False))     # 统计需复核的题数

    summary = {                                         # 组装汇总字典
        "total_score":        total_score,              # 总分
        "full_score":         full_score,               # 总满分
        "score_rate":         score_rate,               # 得分率
        "needs_review_count": needs_review_count,       # 需复核题数
        "by_question":        all_results,              # 逐题结果（已按题号排好序）
    }

    logger.info(                                        # 记一条汇总日志
        "aggregate_results.done",
        total_score=total_score, full_score=full_score,
        score_rate=score_rate, needs_review=needs_review_count,
    )

    return {"pre_review_summary": summary}              # 返回汇总，写回 state
# ──────────────────────────────────────────────────────────────
# 节点5：analyze_weak_points — 知识薄弱点分析
# ──────────────────────────────────────────────────────────────

async def analyze_weak_points_node(state: ExamState) -> dict:
    """
    知识薄弱点分析节点。

    读取 state：
        state["pre_review_summary"]["by_question"]  逐题批改结果（来自 6.8.2 汇总）
        state["submission_id"]                      提交记录 ID（仅用于日志）
    返回：
        weak_points          薄弱知识点列表（每个含 tag/wrong_count/question_nos/suggestion）
        weak_points_summary  整体评价文字

    两条路径：
        路径1：有 knowledge_tag 的失分题 → 按标签直接聚合（规则，不用 LLM）
        路径2：失分题整体交 LLM → 推断无标签题的知识点 + 为各薄弱点生成复习建议
    两路合并去重，按 wrong_count 降序排列。
    """
    all_results = state.get("pre_review_summary", {}).get("by_question", [])  # 取逐题结果

    # 收集失分题（得分 < 满分就算失分，满分题不算薄弱）
    wrong_questions = [
        r for r in all_results
        if r.get("score", 0) < r.get("full_score", 1)
    ]
    # print(f'wrong_questions: {wrong_questions}')
    if not wrong_questions:                             # 全部答对：没有薄弱点
        # logger.info("analyze_weak_points.no_wrong_questions", submission_id=state["submission_id"])
        return {
            "weak_points":         [],                  # 空薄弱点列表
            "weak_points_summary": "本次试卷全部答对，表现优秀！",
        }
    # ── 路径1：有标签 → 按 knowledge_tag 直接聚合 ─────────────────
    tagged   = [r for r in wrong_questions if r.get("knowledge_tag")]      # 有知识点标签的失分题
    untagged = [r for r in wrong_questions if not r.get("knowledge_tag")]  # 没有标签的失分题

    tagged_weak: dict[str, dict] = {}                   # 标签 → 聚合结果
    for r in tagged:                                    # 逐道有标签的失分题
        tag = r["knowledge_tag"]                        # 这道题的知识点
        if tag not in tagged_weak:                      # 第一次见这个标签：建一个空聚合项
            tagged_weak[tag] = {
                "tag":          tag,
                "wrong_count":  0,                      # 该知识点错题数
                "total_count":  0,                      # 该知识点涉及题数
                "question_nos": [],                     # 涉及的题号
                "suggestion":   "",                     # 复习建议（稍后由 LLM 补）
            }
        tagged_weak[tag]["wrong_count"]  += 1           # 错题数 +1
        tagged_weak[tag]["total_count"]  += 1           # 题数 +1
        tagged_weak[tag]["question_nos"].append(r["question_no"])  # 记下题号

    # ── 路径2：失分题整体交 LLM（推断知识点 + 生成 suggestion / summary）──
    llm_weak_points: list[dict] = []                    # LLM 给出的薄弱点列表
    llm_summary = ""                                    # LLM 给出的整体评价

    questions_for_llm = tagged + untagged               # 全部失分题都给 LLM（有标签的也要 LLM 补建议）
    if questions_for_llm:                               # 有失分题才调 LLM
        wrong_desc = "\n".join([                        # 把失分题拼成给 LLM 看的描述文本
            f"第{r['question_no']}题（{r['question_type']}，{r['score']}/{r['full_score']}分）："
            f"\n  题目：{r.get('content', r.get('ai_feedback', ''))[:200]}"   # 题目（截断 200 字）
            f"\n  AI反馈：{r.get('ai_feedback', '')[:150]}"                    # AI 反馈（截断 150 字）
            for r in questions_for_llm
        ])

        prompt         = WEAK_POINTS_ANALYSIS_PROMPT.format(wrong_questions=wrong_desc)  # 拼 Prompt
        print(f'len(prompt):{len(prompt)}')
        structured_llm = get_structured_llm("exam_subjective", WeakPointsReport)         # 结构化输出模型

        try:                                            # LLM 可能失败，包 try
            report: WeakPointsReport = await structured_llm.ainvoke([   # 调用 → 得到 WeakPointsReport
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            llm_weak_points = [wp.model_dump() for wp in report.weak_points]  # 转成 dict 列表
            llm_summary     = report.overall_summary    # 整体评价
        except Exception as e:                          # LLM 失败：降级，不崩
            logger.warning("analyze_weak_points.llm_failed", error=str(e))
            llm_summary = "能力缺口分析失败，请招聘官根据答题情况人工判断。"

    # ── 合并两路结果，去重 ────────────────────────────────────
    # 优先用规则聚合的结果（准确），LLM 只负责补 suggestion 和无标签题的知识点。
    merged_tags       = set(tagged_weak.keys())         # 已被规则聚合的标签集合
    final_weak_points = []                              # 最终薄弱点列表

    for llm_wp in llm_weak_points:                      # 遍历 LLM 给的每个薄弱点
        tag = llm_wp.get("tag", "")
        if tag in tagged_weak:                          # 这个标签规则已聚合 → 只取 LLM 的建议
            tagged_weak[tag]["suggestion"] = llm_wp.get("suggestion", "")
        elif tag not in merged_tags:                    # 纯 LLM 推断出的新知识点（无标签题）
            final_weak_points.append(llm_wp)            # 直接收入
            merged_tags.add(tag)

    # 规则聚合的结果（已补上 suggestion）也加入最终列表
    for wp in tagged_weak.values():
        if not wp["suggestion"]:                        # LLM 没给建议时给个兜底建议
            wp["suggestion"] = f"建议重点复习 {wp['tag']} 相关知识点。"
        final_weak_points.append(wp)

    # 按 wrong_count 降序排（错得最多的知识点排最前）
    final_weak_points.sort(key=lambda x: x.get("wrong_count", 0), reverse=True)

    logger.info(                                        # 记一条日志
        "analyze_weak_points.done",
        # submission_id=state["submission_id"],
        weak_count=len(final_weak_points),
    )

    return {                                            # 返回薄弱点 + 整体评价，写回 state
        "weak_points":         final_weak_points,
        "weak_points_summary": llm_summary or "已完成薄弱点分析，请查看详情。",
    }
# backend/agents/exam/nodes.py（接 6.8）
# ──────────────────────────────────────────────────────────────
# 节点6：notify_teacher — 低置信度时通知招聘官
# ──────────────────────────────────────────────────────────────

async def notify_teacher_node(state: ExamState) -> dict:
    """
    仅在存在低置信度题目时推进到 pending_review，等待招聘官确认。

    职责：
        1. 更新 exam_submissions.status = 'pending_review' #待审核
        2. 记录日志（生产环境可在此接入企业微信/钉钉推送）
    """
    needs_review = _needs_manual_review(state)
    if not needs_review:
        logger.info("notify_recruiter.skipped", submission_id=state["submission_id"])
        return {"teacher_notified": False}

    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    UPDATE exam_submissions
                    SET status = 'pending_review', updated_at = NOW()
                    WHERE id = :submission_id
                      AND tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND status = 'ai_processing'
                """),
                {"submission_id": state["submission_id"],
                 "tenant_id": state["tenant_id"], "student_id": state["student_id"]},
            )
            if result.rowcount != 1:
                raise RuntimeError("笔试提交不存在、归属不匹配或状态已变化")
            await session.execute(
                text("DELETE FROM exam_reviews WHERE submission_id = :submission_id"),
                {"submission_id": state["submission_id"]},
            )
            for item in state.get("pre_review_summary", {}).get("by_question", []):
                await session.execute(
                    text("""
                        INSERT INTO exam_reviews (
                            id, submission_id, question_id, question_type,
                            knowledge_tag, student_answer, ai_score, ai_feedback,
                            ai_raw_result, final_score, needs_review
                        ) VALUES (
                            :id, :submission_id, :question_id, :question_type,
                            :knowledge_tag, :student_answer, :ai_score, :ai_feedback,
                            :ai_raw_result, :final_score, :needs_review
                        )
                    """),
                    {
                        "id": str(uuid.uuid4()), "submission_id": state["submission_id"],
                        "question_id": item["question_id"], "question_type": item["question_type"],
                        "knowledge_tag": item.get("knowledge_tag", ""),
                        "student_answer": item.get("student_answer", ""),
                        "ai_score": item.get("score", 0), "ai_feedback": item.get("ai_feedback", ""),
                        "ai_raw_result": json.dumps(item, ensure_ascii=False),
                        "final_score": item.get("score", 0), "needs_review": item.get("needs_review", False),
                    },
                )
            await session.execute(
                text("""UPDATE exam_submissions
                         SET weak_points=:weak_points, weak_points_summary=:summary
                         WHERE id=:submission_id AND tenant_id=:tenant_id"""),
                {"submission_id": state["submission_id"], "tenant_id": state["tenant_id"],
                 "weak_points": json.dumps(state.get("weak_points", []), ensure_ascii=False),
                 "summary": state.get("weak_points_summary", "")},
            )
            await ApplicationService(session).record_exam_result(
                application_id=state["application_id"],
                tenant_id=state["tenant_id"],
                submission_id=state["submission_id"],
                needs_review=True,
            )

    logger.info("notify_recruiter.done", submission_id=state["submission_id"])
    return {"teacher_notified": True}
# ──────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────
# 节点7：teacher_review — Human-in-the-Loop 暂停点
# ──────────────────────────────────────────────────────────────

async def teacher_review_node(state: ExamState) -> dict:
    """
    Human-in-the-Loop 核心节点。

    interrupt(display_data) 做两件事：
        1. 把 display_data 暴露给外部（招聘官可通过 GET 接口读取）
        2. 冻结图执行，State 自动保存到 MemorySaver

    图在此暂停，直到招聘官通过 POST /confirm 传入 Command(resume=decision) 恢复。
    恢复后，interrupt() 的返回值就是 decision（teacher_decision 字段）。
    """
    if not _needs_manual_review(state):
        logger.info("recruiter_review.auto_approved", submission_id=state["submission_id"])
        return {"teacher_decision": {
            "action": "approve", "modifications": [], "teacher_id": ""
        }}

    display_data = {
        "submission_id":       state["submission_id"],
        "student_id":          state["student_id"],
        "pre_review_summary":  state.get("pre_review_summary", {}),
        "weak_points":         state.get("weak_points", []),
        "weak_points_summary": state.get("weak_points_summary", ""),
        "message":             "存在低置信度题目，请招聘官复核后确认。",
    }

    # interrupt() 在此暂停图执行
    teacher_decision = interrupt(display_data)

    logger.info(
        "teacher_review.resumed",
        submission_id=state["submission_id"],
        action=teacher_decision.get("action", "unknown"),
    )

    return {"teacher_decision": teacher_decision}


def _needs_manual_review(state: ExamState) -> bool:
    """只要存在低置信度或降级题目，就交由招聘官复核。"""
    return state.get("pre_review_summary", {}).get("needs_review_count", 0) > 0
# ──────────────────────────────────────────────────────────────
# 节点8：apply_teacher_decision — 合并教师修改
# ──────────────────────────────────────────────────────────────

async def apply_teacher_decision_node(state: ExamState) -> dict:
    """
    将招聘官决策合并到批改结果中。

    approve：直接把 AI 分数作为最终分数
    modify： 按 modifications 列表覆盖指定题目的得分和评语
    """
    decision      = state.get("teacher_decision", {})
    action        = decision.get("action", "approve")
    modifications = decision.get("modifications", [])

    # 取 pre_review_summary 中的完整题目列表作为基础
    all_results = list(state.get("pre_review_summary", {}).get("by_question", []))

    # 每道题先用 AI 分数初始化 final_score
    for r in all_results:
        r["final_score"] = r.get("score", 0)

    # modify 时，按 question_id 找到对应题目，覆盖分数和评语
    if action == "modify" and modifications:
        id_to_idx = {r["question_id"]: i for i, r in enumerate(all_results)}
        for mod in modifications:
            qid = mod.get("question_id")
            if qid in id_to_idx:
                idx = id_to_idx[qid]
                if "new_score" in mod:
                    full_score = int(all_results[idx].get("full_score", 0))
                    new_score = max(0, min(int(mod["new_score"]), full_score))
                    all_results[idx]["teacher_score"] = new_score
                    all_results[idx]["final_score"]   = new_score
                if "comment" in mod:
                    all_results[idx]["teacher_comment"] = mod["comment"]
                all_results[idx]["reviewed_by"] = decision.get("teacher_id", "")

    logger.info(
        "apply_teacher_decision.done",
        action=action,
        modifications_count=len(modifications),
    )

    return {"final_results": all_results}

# ──────────────────────────────────────────────────────────────
# 节点9：publish_results — 发布批改结果
# ──────────────────────────────────────────────────────────────

async def publish_results_node(state: ExamState) -> dict:
    """
    将最终批改结果写入数据库，供招聘方内部查看。

    写入逻辑：
        exam_reviews  ── 先删后插（幂等），每道题一行
        exam_submissions ── 更新 status='published' + weak_points JSON
    """
    final_results = state.get("final_results", [])
    submission_id = state["submission_id"]
    teacher_id    = state.get("teacher_decision", {}).get("teacher_id", "") or None
    weak_points   = state.get("weak_points", [])

    async with AsyncSessionLocal() as session:
        async with session.begin():

            # ── 逐题写入 exam_reviews ─────────────────────────
            for r in final_results:
                # 先删后插：避免重复发布时产生重复记录
                await session.execute(
                    text("""
                        DELETE FROM exam_reviews
                        WHERE submission_id = :submission_id
                          AND question_id   = :question_id
                    """),
                    {"submission_id": submission_id, "question_id": r["question_id"]},
                )
                await session.execute(
                    text("""
                        INSERT INTO exam_reviews (
                            id, submission_id, question_id, question_type,
                            knowledge_tag, student_answer,
                            ai_score, ai_feedback, ai_raw_result,
                            teacher_score, teacher_comment, final_score,
                            needs_review, reviewed_by, reviewed_at
                        ) VALUES (
                            :id, :submission_id, :question_id, :question_type,
                            :knowledge_tag, :student_answer,
                            :ai_score, :ai_feedback, :ai_raw_result,
                            :teacher_score, :teacher_comment, :final_score,
                            :needs_review, :reviewed_by, NOW()
                        )
                    """),
                    {
                        "id":              str(uuid.uuid4()),
                        "submission_id":   submission_id,
                        "question_id":     r["question_id"],
                        "question_type":   r["question_type"],
                        "knowledge_tag":   r.get("knowledge_tag", ""),
                        "student_answer":  r.get("student_answer", ""),
                        "ai_score":        r.get("score", 0),
                        "ai_feedback":     r.get("ai_feedback", ""),
                        "ai_raw_result":   json.dumps(r),           # 完整原始结果存 JSON
                        "teacher_score":   r.get("teacher_score"),  # None 表示未修改
                        "teacher_comment": r.get("teacher_comment"),
                        "final_score":     r.get("final_score", r.get("score", 0)),
                        "needs_review":    r.get("needs_review", False),
                        "reviewed_by":     teacher_id,
                    },
                )

            # ── 更新提交状态为已发布 ─────────────────────────
            result = await session.execute(
                text("""
                    UPDATE exam_submissions
                    SET status               = 'published',
                        published_at         = NOW(),
                        updated_at           = NOW(),
                        weak_points          = :weak_points,
                        weak_points_summary  = :weak_points_summary
                    WHERE id = :submission_id
                      AND tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND status IN ('ai_processing', 'pending_review')
                """),
                {
                    "submission_id":       submission_id,
                    "weak_points":         json.dumps(weak_points),
                    "weak_points_summary": state.get("weak_points_summary", ""),
                    "tenant_id": state["tenant_id"],
                    "student_id": state["student_id"],
                },
            )
            if result.rowcount != 1:
                raise RuntimeError("笔试提交不存在、归属不匹配或状态已变化")
            await ApplicationService(session).record_exam_result(
                application_id=state["application_id"],
                tenant_id=state["tenant_id"],
                submission_id=submission_id,
                needs_review=False,
            )

    total_final = sum(r.get("final_score", 0) for r in final_results)
    full_score  = sum(r.get("full_score", 0) for r in final_results)

    logger.info(
        "publish_results.done",
        submission_id=submission_id,
        final_score=total_final,
        weak_points_count=len(weak_points),
    )

    # structured_output 供 API 层直接返回给招聘官确认接口
    return {
        "published": True,
        "structured_output": {
            "submission_id":       submission_id,
            "final_score":         total_final,
            "full_score":          full_score,
            "score_rate":          round(total_final / full_score, 4) if full_score else 0,
            "weak_points":         weak_points,
            "weak_points_summary": state.get("weak_points_summary", ""),
            "published":           True,
        },
    }
if __name__ == '__main__':
    import asyncio
    async def _run_all():
        state = {"word_file_path": "./student_answer.docx"}
        results = await parse_word_node(state)
        results["exam_id"] = "e0000001-0000-0000-0000-000000000001"
        merge_results = await load_questions_meta_node(results)
        all_results1 = await run_three_tracks_node(merge_results)
        all_results2 = await aggregate_results_node(all_results1)
        all_results3 = await analyze_weak_points_node(all_results2)
        print(f'all_results3:{all_results3}')
        return all_results3
    asyncio.run(_run_all())
