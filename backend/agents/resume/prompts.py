SYSTEM_PROMPT = """你是企业招聘团队的简历初筛助手。只依据岗位信息和简历中明确出现的内容进行判断。
不得臆测候选人的能力或经历，不得依据姓名、性别、年龄、民族、籍贯、婚育、照片等受保护或与岗位无关的信息评分。
每个判断都要能回到简历证据；信息缺失应标记为待核验，而不是直接推断为不合格。你的输出仅供招聘人员辅助决策。"""

EXTRACT_STRUCTURED_PROMPT = """请从以下简历文本中提取结构化信息。

【简历原文】
{resume_text}

要求：忠实保留原文，不补写经历；技术栈逐项拆分；时间尽量统一为 YYYY.MM - YYYY.MM；
无法提取的字符串填空字符串、列表填空列表；量化亮点仅提取原文中确实含数字的句子。"""

_DIMENSION_TEMPLATE = """请评审候选人简历在【{dimension}】维度与目标岗位的匹配情况。

【岗位名称】{position_title}
【岗位 JD】
{job_description}
【结构化岗位要求】
{job_requirements}
【本维度关注点】{focus}
【简历摘要】
{structured_summary}
【简历原文】
{resume_text}

请给出 0-100 分。90-100 表示有多项明确且可核验的强证据；70-89 表示主要要求基本匹配；
50-69 表示部分匹配且存在重要信息缺口；30-49 表示多数关键要求缺少证据；0-29 表示有明确证据表明严重不匹配。
issues 必须写具体缺口或待核验点，suggestions 写招聘人员后续面试可核验的问题。不得使用受保护属性作为依据。"""

def _dimension_prompt(name: str) -> str:
    return _DIMENSION_TEMPLATE.replace("{dimension}", name)

DIMENSION_REVIEW_PROMPTS = {
    "basic_eligibility": _dimension_prompt("基础条件"),
    "core_skill_match": _dimension_prompt("核心技能匹配"),
    "project_relevance": _dimension_prompt("项目相关性与深度"),
    "work_experience": _dimension_prompt("工作经历匹配"),
    "achievement_credibility": _dimension_prompt("成果可信度"),
    "completeness_risk": _dimension_prompt("表达完整性与风险"),
}

DIAGNOSE_ISSUES_PROMPT = """请整理供招聘人员使用的简历核验清单。

【岗位名称】{position_title}
【岗位 JD】{job_description}
【岗位要求】{job_requirements}
【简历摘要】{structured_summary}
【简历原文】{resume_text}
【各维度发现】{raw_issues}

每项必须指向具体简历位置或明确写“简历未提供”，并给出可在后续面试核验的问题。
high 表示影响岗位核心要求，medium 表示重要补充核验，low 表示一般信息缺口。合并重复项，总数控制在 3-12 条。"""

GENERATE_SUMMARY_PROMPT = """请生成供招聘人员查看的简历初筛报告。

【岗位名称】{position_title}
【岗位 JD】{job_description}
【岗位要求】{job_requirements}
【候选人简历摘要】{structured_summary}
【各维度评分】{scores_summary}
【综合得分】{weighted_score} / 100
【主要风险和缺口】{high_issues}

要求：
1. highlights 和 evidence 只能引用简历中存在的事实；
2. core_improvements 表示与岗位要求相比的主要缺口；risk_flags 表示矛盾、缺失或需要核验的信息；
3. decision 只能是 pass、reject、manual_review，但它只是辅助建议，不能引用未提供的信息；
4. 若解析信息过少、证据相互矛盾或无法可靠判断，manual_review_required 必须为 true；
5. 不使用姓名、性别、年龄、民族、籍贯、婚育、照片等信息作判断。"""

DIAGNOSE_THINK_PROMPT = """请基于以下评分和问题，分析哪些岗位核心要求已有简历证据，哪些仍需人工核验。
不要使用受保护属性，不要推断简历未陈述的事实。

【评分汇总】
{dimension_scores_summary}
【问题列表】
{raw_issues}

用 3-6 句话输出内部分析。"""
