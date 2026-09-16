# backend/agents/resume/nodes.py

import asyncio
import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from backend.agents.resume.state import (
    ResumeState, ResumeStructured, DimensionScore, IssueList, ResumeSummary,
)
from backend.agents.resume.prompts import (
    SYSTEM_PROMPT, EXTRACT_STRUCTURED_PROMPT, DIMENSION_REVIEW_PROMPTS,
    DIAGNOSE_ISSUES_PROMPT, GENERATE_SUMMARY_PROMPT, DIAGNOSE_THINK_PROMPT,
)
from backend.core.llm_factory import get_structured_llm, get_llm
from backend.core.logger import get_logger
from backend.core.application_service import ApplicationService, ResumeDecision
from backend.config import get_settings
from backend.core.object_storage import get_object_storage
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)


# ── 六维度定义（名称 / 权重 / 评分侧重）。权重之和 = 1.0 ──
SIX_DIMENSIONS = [
    {"key": "basic_eligibility", "name": "基础条件", "weight": 0.10,
     "focus": "只核对 JD 明确要求的学历、专业和工作年限，不评价年龄等受保护属性"},
    {"key": "core_skill_match", "name": "核心技能匹配", "weight": 0.25,
     "focus": "必需技能和优先技能是否有明确的使用证据"},
    {"key": "project_relevance", "name": "项目相关性与深度", "weight": 0.25,
     "focus": "项目场景、个人职责、技术难点是否与岗位职责相关"},
    {"key": "work_experience", "name": "工作经历匹配", "weight": 0.15,
     "focus": "岗位职责、业务场景和经验时长是否满足 JD"},
    {"key": "achievement_credibility", "name": "成果可信度", "weight": 0.15,
     "focus": "量化成果是否有上下文，贡献归属和技术描述是否一致"},
    {"key": "completeness_risk", "name": "表达完整性与风险", "weight": 0.10,
     "focus": "关键岗位信息是否缺失，时间线或技术描述是否矛盾并需要核验"},
]


async def upload_to_minio_node(state: ResumeState) -> dict:
    """Persist the uploaded PDF before the temporary local file is removed."""
    settings = get_settings()
    if not settings.object_storage_enabled:
        logger.info("upload_to_minio.skip", review_id=state["review_id"], reason="disabled")
        return {}
    object_key = state.get("pdf_minio_path") or (
        f"resumes/{state['student_id']}/{state['review_id']}.pdf"
    )
    await get_object_storage().put_file(object_key, state["pdf_local_path"])
    logger.info("upload_to_minio.done", review_id=state["review_id"], object_key=object_key)
    return {"pdf_minio_path": object_key}


async def download_pdf_node(state: ResumeState) -> dict:
    """Use the upload temp file when available, otherwise restore it from storage."""
    local_path = state["pdf_local_path"]
    if os.path.exists(local_path):
        logger.info("download_pdf.skip", review_id=state["review_id"], reason="local_file_exists")
        return {}
    if not get_settings().object_storage_enabled or not state.get("pdf_minio_path"):
        raise FileNotFoundError("简历临时文件不存在，且对象存储不可用")
    await get_object_storage().get_file(state["pdf_minio_path"], local_path)
    logger.info("download_pdf.done", review_id=state["review_id"], object_key=state["pdf_minio_path"])
    return {}


# ── 节点③：extract_text —— PDF 文本提取（双栏处理 + 线程池）──
def _sync_extract_text(pdf_path: str) -> dict:
    """同步 PDF 文本提取（线程池中运行），处理双栏布局。"""
    import fitz                                       # PyMuPDF
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    all_text_parts = []

    for page in doc:
        blocks = page.get_text("blocks")              # 每块 (x0,y0,x1,y1,text,no,type)
        text_blocks = [b for b in blocks if b[6] == 0]   # type==0 文字块
        if not text_blocks:
            continue
        page_width = page.rect.width
        midpoint   = page_width / 2
        left_blocks  = [b for b in text_blocks if b[0] < midpoint - 20]
        right_blocks = [b for b in text_blocks if b[0] >= midpoint - 20]
        is_two_column = (
            len(left_blocks) >= 2 and len(right_blocks) >= 2
            and len(right_blocks) / max(len(text_blocks), 1) > 0.3
        )
        if is_two_column:                             # 双栏：左栏读完读右栏
            left_sorted  = sorted(left_blocks,  key=lambda b: b[1])
            right_sorted = sorted(right_blocks, key=lambda b: b[1])
            page_text = (
                "\n".join(b[4].strip() for b in left_sorted  if b[4].strip())
                + "\n"
                + "\n".join(b[4].strip() for b in right_sorted if b[4].strip())
            )
        else:                                         # 单栏：按 y 从上到下
            sorted_blocks = sorted(text_blocks, key=lambda b: b[1])
            page_text = "\n".join(b[4].strip() for b in sorted_blocks if b[4].strip())
        all_text_parts.append(page_text)

    doc.close()
    raw_text = "\n\n---PAGE BREAK---\n\n".join(all_text_parts)
    return {"raw_text": raw_text, "page_count": page_count}


async def extract_text_node(state: ResumeState) -> dict:
    """异步节点：线程池跑同步解析，避免阻塞事件循环。"""
    pdf_path = state["pdf_local_path"]
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _sync_extract_text, pdf_path)
        raw_text, page_count = result["raw_text"], result["page_count"]
        if len(raw_text.strip()) < 200:               # 疑似扫描件/图片 PDF，仅告警
            logger.warning("extract_text.text_too_short", text_length=len(raw_text.strip()))
        logger.info("extract_text.done", page_count=page_count, text_length=len(raw_text))
        return {"raw_text": raw_text, "page_count": page_count}
    except Exception as e:
        logger.error("extract_text.failed", error=str(e))
        raise


# ── 节点④：extract_structured —— LLM 结构化提取 ──
async def extract_structured_node(state: ResumeState) -> dict:
    """用 LLM Function Calling 把文本提取成结构化简历。"""
    raw_text = state["raw_text"]
    text_for_llm = raw_text[:4000] if len(raw_text) > 4000 else raw_text   # 超长截断
    prompt = EXTRACT_STRUCTURED_PROMPT.format(resume_text=text_for_llm)
    structured_llm = get_structured_llm("resume", ResumeStructured)
    structured_dict = None
    for attempt in range(2):                           # 结构化输出偶发返回 None → 判空+重试
        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            if result is None:
                raise ValueError("structured output returned None")
            structured_dict = result.model_dump()
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("extract_structured.retry", error=str(e))
                await asyncio.sleep(1)
            else:
                logger.warning("extract_structured.failed", error=str(e))
    if structured_dict is None:
        structured_dict = ResumeStructured(name="未能提取").model_dump()   # 降级空结构
    logger.info("extract_structured.done",
                name=structured_dict.get("name", ""),
                projects_count=len(structured_dict.get("projects", [])))
    return {"structured": structured_dict, "fallback_used": structured_dict["name"] == "未能提取"}


# ── 节点⑤：run_six_dimensions —— 六维度并行评审 ──
async def run_six_dimensions_node(state: ResumeState) -> dict:
    """六维度并行评审：asyncio.gather 同时评，算加权综合分。"""
    raw_text   = state["raw_text"]
    structured = state.get("structured") or {}
    structured_summary = _build_structured_summary(structured)

    async def review_one_dimension(dim: dict) -> dict:
        prompt_template = DIMENSION_REVIEW_PROMPTS.get(dim["key"], "")
        if not prompt_template:
            return _empty_dimension_score(dim)
        prompt = prompt_template.format(
            resume_text=raw_text[:3000], structured_summary=structured_summary, focus=dim["focus"],
            position_title=state["position_title"],
            job_description=state["job_description"],
            job_requirements=json.dumps(state.get("job_requirements") or {}, ensure_ascii=False),
        )
        for attempt in range(2):                      # 最多 2 次尝试
            try:
                structured_llm = get_structured_llm("resume", DimensionScore)
                result: DimensionScore = await structured_llm.ainvoke([
                    SystemMessage(content=SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ])
                d = result.model_dump()
                d["dimension"], d["weight"], d["key"] = dim["name"], dim["weight"], dim["key"]
                return d
            except Exception as e:
                if attempt == 0:
                    logger.warning("six_dimensions.dimension_retry", dimension=dim["name"], error=str(e))
                    await asyncio.sleep(1)
                else:
                    logger.warning("six_dimensions.dimension_failed", dimension=dim["name"], error=str(e))
                    return _empty_dimension_score(dim)

    tasks = [review_one_dimension(dim) for dim in SIX_DIMENSIONS]
    dimension_scores = await asyncio.gather(*tasks)              # 并行
    weighted_score = sum(d["score"] * d["weight"] for d in dimension_scores)
    logger.info("six_dimensions.done", weighted_score=round(weighted_score, 2),
                scores={d["key"]: d["score"] for d in dimension_scores})
    fallback_used = state.get("fallback_used", False) or any(
        "人工复核" in "".join(d.get("issues", [])) for d in dimension_scores
    )
    return {"dimension_scores": list(dimension_scores), "weighted_score": round(weighted_score, 2),
            "fallback_used": fallback_used}


def _build_structured_summary(structured: dict) -> str:
    """把结构化数据浓缩成几行摘要，供评审使用（省 token）。"""
    lines = []
    if structured.get("name"):
        lines.append(f"姓名：{structured['name']}")
    if structured.get("target_position"):
        lines.append(f"求职意向：{structured['target_position']}")
    if structured.get("education"):
        edu = structured["education"][0]
        lines.append(f"最高学历：{edu.get('school','')} {edu.get('major','')} {edu.get('degree','')}")
    if structured.get("skills_list"):
        lines.append(f"技术栈：{', '.join(structured['skills_list'][:10])}")
    if structured.get("projects"):
        proj_names = [p.get("name", "") for p in structured["projects"]]
        lines.append(f"项目数量：{len(structured['projects'])} 个（{', '.join(proj_names[:3])}）")
    if structured.get("work_experience"):
        lines.append(f"工作经历：{len(structured['work_experience'])} 段")
    return "\n".join(lines) if lines else "（结构化提取失败，请基于原文评审）"


def _empty_dimension_score(dim: dict) -> dict:
    """维度评审失败时的降级结果。"""
    return {"key": dim["key"], "dimension": dim["name"], "score": 50, "weight": dim["weight"],
            "issues": ["该维度评审失败，建议人工复核"], "suggestions": []}


# ── 节点⑥：diagnose_issues —— 逐条问题诊断 ──
async def diagnose_issues_node(state: ResumeState) -> dict:
    """汇总维度问题 → Think 前置推理 → 结构化生成问题清单 → 按优先级排序。"""
    dimension_scores = state.get("dimension_scores", [])
    raw_text         = state["raw_text"]
    structured       = state.get("structured") or {}

    all_raw_issues = []
    for dim in dimension_scores:
        for issue_text in dim.get("issues", []):
            all_raw_issues.append(f"[{dim['dimension']}] {issue_text}")
    raw_issues_text = "\n".join(f"- {i}" for i in all_raw_issues) or "（暂无）"

    reasoning_trace = ""                              # Think 前置推理（可失败）
    try:
        dimension_scores_summary = "\n".join(
            f"- {d['dimension']}：{d['score']}分 — 问题：{', '.join(d.get('issues', [])[:2])}"
            for d in dimension_scores
        )
        think_prompt = DIAGNOSE_THINK_PROMPT.format(
            dimension_scores_summary=dimension_scores_summary, raw_issues=raw_issues_text)
        think_llm = get_llm("resume", temperature=0)
        think_resp = await think_llm.ainvoke([HumanMessage(content=think_prompt)])
        reasoning_trace = (
            think_resp.text if hasattr(think_resp, "text") and not callable(think_resp.text)
            else str(think_resp.content)
        ).strip()
    except Exception as e:
        logger.warning("diagnose_think.failed", error=str(e))

    think_context = f"\n\n【诊断前宏观分析】\n{reasoning_trace}" if reasoning_trace else ""

    prompt = DIAGNOSE_ISSUES_PROMPT.format(
        resume_text=raw_text[:3000],
        structured_summary=_build_structured_summary(structured),
        raw_issues=raw_issues_text,
        position_title=state["position_title"],
        job_description=state["job_description"],
        job_requirements=json.dumps(state.get("job_requirements") or {}, ensure_ascii=False),
    ) + think_context

    try:
        structured_llm = get_structured_llm("resume", IssueList)
        result: IssueList = await structured_llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        issues = [item.model_dump() for item in result.items]
    except Exception as e:
        logger.warning("diagnose_issues.failed", error=str(e))
        issues = [                                    # 降级：用维度问题，统一 medium
            {"priority": "medium", "dimension": dim["dimension"], "description": issue,
             "location": "简历全文", "suggestion": "由招聘人员在后续环节人工核验"}
            for dim in dimension_scores for issue in dim.get("issues", [])
        ]

    priority_order = {"high": 0, "medium": 1, "low": 2}          # 按优先级排序
    issues.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 2))
    logger.info("diagnose_issues.done", total=len(issues),
                high=sum(1 for i in issues if i.get("priority") == "high"))
    return {"issues": issues}


# ── 节点⑦：generate_summary —— 整体评价 ──
async def generate_summary_node(state: ResumeState) -> dict:
    """综合结构化信息、评分和证据，生成招聘方初筛报告。"""
    structured       = state.get("structured") or {}
    dimension_scores = state.get("dimension_scores", [])
    issues           = state.get("issues", [])
    weighted_score   = state.get("weighted_score", 0.0)

    high_issues = [i["description"] for i in issues if i.get("priority") == "high"][:5]
    high_issues_text = "\n".join(f"- {i}" for i in high_issues) or "（无高优先级问题）"
    scores_text = "\n".join(
        f"- {d['dimension']}：{d['score']}分（权重{int(d['weight'] * 100)}%）" for d in dimension_scores)

    prompt = GENERATE_SUMMARY_PROMPT.format(
        structured_summary=_build_structured_summary(structured),
        scores_summary=scores_text, weighted_score=round(weighted_score, 1),
        high_issues=high_issues_text,
        position_title=state["position_title"],
        job_description=state["job_description"],
        job_requirements=json.dumps(state.get("job_requirements") or {}, ensure_ascii=False),
    )
    structured_llm = get_structured_llm("resume", ResumeSummary)
    summary_dict = None
    for attempt in range(2):                           # 结构化输出偶发返回 None → 判空+重试
        try:
            result = await structured_llm.ainvoke([
                SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
            if result is None:
                raise ValueError("structured output returned None")
            summary_dict = result.model_dump()
            break
        except Exception as e:
            if attempt == 0:
                logger.warning("generate_summary.retry", error=str(e))
                await asyncio.sleep(1)
            else:
                logger.warning("generate_summary.failed", error=str(e))
    if summary_dict is None:
        summary_dict = {                              # 降级默认评价
            "highlights": ["简历内容已完整提交"],
            "core_improvements": high_issues[:2] if high_issues else ["请参考各维度建议修改"],
            "overall_comment": f"综合评分 {round(weighted_score, 1)} 分，请参考各维度详细反馈。",
            "fit_assessment": "自动匹配评估不可用，需要招聘人员复核",
            "decision": "manual_review",
            "evidence": [],
            "risk_flags": ["大模型报告生成失败"],
            "manual_review_required": True,
        }
    logger.info("generate_summary.done", highlights_count=len(summary_dict.get("highlights", [])))
    return {"summary": summary_dict}


# ── 节点⑧：save_results —— 持久化结果 ──
async def save_results_node(state: ResumeState) -> dict:
    """把完整结果写入 resume_reviews（JSONB 字段），清理临时文件。"""
    review_id = state["review_id"]

    # 留一份完整结果给上层（API 可直接用）
    structured_output = {
        "review_id": review_id, "student_id": state["student_id"],
        "structured": state.get("structured"),
        "weighted_score": state.get("weighted_score", 0),
        "dimension_scores": state.get("dimension_scores", []),
        "issues": state.get("issues", []),
        "summary": state.get("summary"),
    }

    decision = _determine_resume_decision(state)
    structured_output["decision"] = decision.value

    async with AsyncSessionLocal() as session:        # 结果和申请状态在同一事务提交
        try:
            result = await session.execute(
                text("""
                    UPDATE resume_reviews
                    SET structured_data = :structured_data,
                        scores          = :scores,
                        issues          = :issues,
                        summary         = :summary,
                        status          = 'done',
                        updated_at      = NOW()
                    WHERE id = :review_id
                      AND tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND status = 'processing'
                """),
                {
                    # JSONB 列：先 json.dumps 转 JSON 字符串；ensure_ascii=False 保留中文原文
                    "structured_data": json.dumps(state.get("structured"), ensure_ascii=False),
                    "scores": json.dumps(
                        {"dimension_scores": state.get("dimension_scores", []),
                         "weighted_score": state.get("weighted_score", 0)},
                        ensure_ascii=False),
                    "issues":  json.dumps(state.get("issues", []), ensure_ascii=False),
                    "summary": json.dumps(state.get("summary"), ensure_ascii=False),
                    "review_id": review_id,
                    "tenant_id": state["tenant_id"],
                    "student_id": state["student_id"],
                },
            )
            if result.rowcount != 1:
                raise RuntimeError("简历评审记录不存在、归属不匹配或状态已变化")
            await ApplicationService(session).apply_resume_result(
                application_id=state["application_id"],
                tenant_id=state["tenant_id"],
                review_id=review_id,
                decision=decision,
            )
            await session.commit()
            logger.info("save_results.db_written", review_id=review_id, decision=decision.value)
        except Exception as e:
            await session.rollback()
            logger.error("save_results.db_failed", error=str(e))
            raise

    # 清理本地临时 PDF
    local_path = state.get("pdf_local_path", "")
    if local_path and os.path.exists(local_path):
        os.remove(local_path)
        logger.info("save_results.tmp_cleaned", path=local_path)

    return {"fallback_used": state.get("fallback_used", False),
            "structured_output": structured_output}


def _determine_resume_decision(state: ResumeState) -> ResumeDecision:
    """可信度优先于分数；通过阈值只取岗位配置，不交给模型决定。"""
    summary = state.get("summary") or {}
    structured = state.get("structured") or {}
    parsing_risk = (
        state.get("fallback_used", False)
        or len((state.get("raw_text") or "").strip()) < 200
        or not structured.get("name")
        or summary.get("manual_review_required", False)
    )
    if parsing_risk:
        return ResumeDecision.MANUAL_REVIEW
    if float(state.get("weighted_score", 0)) >= float(state["resume_pass_score"]):
        return ResumeDecision.PASSED
    return ResumeDecision.REJECTED


# ── 模块自测：实测 PDF 文本提取（离线；其余节点测试见各节）──
if __name__ == "__main__":
    import fitz
    _doc = fitz.open(); _page = _doc.new_page()
    _page.insert_text((50, 60),
        "张三  后端开发\n技能\n熟悉 Java、Spring Boot、Redis\n"
        "项目经历\n电商系统  2023.06 - 2023.12\nQPS 提升 30%",
        fontsize=11, fontname="china-s")
    _doc.save("/tmp/sample_resume.pdf"); _doc.close()
    _r = asyncio.run(extract_text_node({"pdf_local_path": "/tmp/sample_resume.pdf"}))
    print("page_count:", _r["page_count"], "| 含 Spring Boot:", "Spring Boot" in _r["raw_text"])

# ── 模块自测：实测 PDF 文本提取（离线；其余节点测试见各节）──
if __name__ == "__main__":
    # import asyncio
    # from types import SimpleNamespace
    # from backend.agents.resume.state import IssueItem, IssueList, ResumeSummary
    #
    # class FakeThink:  # 假 Think 模型
    #     async def ainvoke(self, messages):
    #         return SimpleNamespace(content="宏观分析：项目深度不足是核心短板",
    #                                text="宏观分析：项目深度不足是核心短板")
    #
    # class FakeStructured:  # 假结构化模型（可返回对象或抛错）
    #     def __init__(self, obj=None, fail=False):
    #         self.obj, self.fail = obj, fail
    #
    #     async def ainvoke(self, messages):
    #         if self.fail:
    #             raise RuntimeError("模拟LLM失败")
    #         return self.obj
    #
    #
    # state = {
    #     "raw_text": "简历原文 " * 50,
    #     "structured": {"name": "张三", "target_position": "后端", "projects": [{"name": "电商"}]},
    #     "dimension_scores": [
    #         {"key": "project_depth", "dimension": "项目深度", "score": 60, "weight": 0.3, "issues": ["项目描述空洞"]},
    #         {"key": "tech_match", "dimension": "技术匹配度", "score": 70, "weight": 0.25, "issues": ["技能无层次"]},
    #     ],
    #     "weighted_score": 64.0,
    # }
    #
    # # ① diagnose 成功：故意乱序优先级，验证排序 high→medium→low
    # get_llm = lambda *a, **k: FakeThink()
    # mixed = IssueList(items=[
    #     IssueItem(priority="low", dimension="结构", description="证书未列", location="末尾", suggestion="补充"),
    #     IssueItem(priority="high", dimension="项目深度", description="无量化", location="项目-电商",
    #               suggestion="加QPS"),
    #     IssueItem(priority="medium", dimension="表达", description="非动词开头", location="项目第1句",
    #               suggestion="改写"),
    # ])
    # get_structured_llm = lambda a, schema: FakeStructured(obj=mixed)
    # r = asyncio.run(diagnose_issues_node(state))
    # print(f'r-->{r}')
    # print("① 排序后优先级:", [i["priority"] for i in r["issues"]])
    #
    # # ② diagnose 降级：结构化 LLM 失败 → 用维度问题兜底
    # get_structured_llm = lambda a, schema: FakeStructured(fail=True)
    # r2 = asyncio.run(diagnose_issues_node(state))
    # print(f'r2-->{r2}')
    # print("② 降级后问题数:", len(r2["issues"]), "| 全 medium:", all(i["priority"] == "medium" for i in r2["issues"]))
    #
    # # ③ generate_summary 成功
    # summary_obj = ResumeSummary(highlights=["有项目经历"], core_improvements=["补充量化"],
    #                             overall_comment="中等水平", fit_assessment="较匹配")
    # get_structured_llm = lambda a, schema: FakeStructured(obj=summary_obj)
    # state["issues"] = r["issues"]
    # r3 = asyncio.run(generate_summary_node(state))
    # print(f'r3-->{r3}')
    # print("③ summary 字段:", list(r3["summary"].keys()), "| fit:", r3["summary"]["fit_assessment"])
    import asyncio, uuid
    from sqlalchemy import text
    from backend.dependencies import AsyncSessionLocal
    rid = str(uuid.uuid4())

    async def main():
        async with AsyncSessionLocal() as s:  # 先插一条 processing 记录
            sid = (await s.execute(text("SELECT id FROM users WHERE username='student01'"))).scalar()
            print(sid)
            await s.execute(text("""INSERT INTO resume_reviews (id, tenant_id, student_id, pdf_minio_path, status)
                                    VALUES (:id, 'tenant_default', :sid, 'resumes/x.pdf', 'processing')"""),
                            {"id": rid, "sid": sid})
            await s.commit()
        #
        await save_results_node({  # 运行节点
            "review_id": rid, "student_id": str(sid), "pdf_local_path": "/tmp/none.pdf",
            "structured": {"name": "张三", "skills_list": ["Java"]}, "weighted_score": 78.5,
            "dimension_scores": [{"key": "project_depth", "dimension": "项目深度", "score": 75, "weight": 0.3}],
            "issues": [{"priority": "high", "description": "无量化"}],
            "summary": {"highlights": ["有项目"], "overall_comment": "中等"},
        })

        async with AsyncSessionLocal() as s:  # 查回验证（用 JSONB 操作符）
            row = (await s.execute(text("""
                                        SELECT status,
                                               (scores ->>'weighted_score') AS ws,
                                               (structured_data ->>'name')  AS nm,
                                               jsonb_array_length(issues)   AS cnt
                                        FROM resume_reviews
                                        WHERE id = :id"""), {"id": rid})).mappings().fetchone()
            print("status:", row["status"], "| 加权分:", row["ws"], "| 姓名:", row["nm"], "| 问题数:", row["cnt"])


    asyncio.run(main())
