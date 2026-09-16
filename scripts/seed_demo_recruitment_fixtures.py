"""Seed clearly marked local-only recruitment fixtures.

This replaces the generic Java demo exam for the existing local test position,
and inserts a small interview bank. It must not be used as production content.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from sqlalchemy import text
from backend.dependencies import AsyncSessionLocal

TENANT = "tenant_default"
POSITION_ID = "b0000001-0000-0000-0000-000000000001"
EXAM_ID = "e0000001-0000-0000-0000-000000000001"
TARGET_POSITION = "大模型应用开发工程师（本地测试）"
QUESTION_IDS = [
    "e0000001-0000-0000-0000-0000000000b1",
    "e0000001-0000-0000-0000-0000000000b2",
    "e0000001-0000-0000-0000-0000000000b3",
    "e0000001-0000-0000-0000-0000000000b4",
    "e0000001-0000-0000-0000-0000000000b5",
]
QUESTIONS = [
    (1, "single_choice", "RAG 系统中，向量数据库主要用于什么？\nA. 保存文本向量并进行相似度检索\nB. 执行 Python 代码\nC. 生成模型参数\nD. 管理用户密码", "A", 5, "RAG基础"),
    (2, "multi_choice", "以下哪些做法有助于提升 RAG 召回质量？（多选）\nA. 合理分块\nB. 元数据过滤\nC. 混合检索\nD. 永远只取一条结果", "ABC", 6, "RAG检索"),
    (3, "judge", "判断：LangGraph 可以用状态和条件边表达多步骤 Agent 工作流。", "正确", 4, "LangGraph"),
    (4, "short_answer", "请说明大模型应用中为什么需要对工具调用设置超时、重试和降级策略。", "", 10, "工程可靠性"),
    (5, "code", "请编写一个 Python 函数，将列表中的重复字符串去重并保持原有顺序。", json.dumps([{ "input": "[a,b,a,c]", "expected_output": "[a,b,c]" }], ensure_ascii=False), 10, "Python基础"),
]
INTERVIEW_QUESTIONS = [
    ("请结合一个项目说明你如何设计 RAG 的切分、召回和重排链路。", "medium", ["RAG", "检索" ]),
    ("LangGraph 中如何表达循环流程？如何避免循环无法结束？", "medium", ["LangGraph", "状态机"]),
    ("如果外部大模型接口连续超时，你会如何设计用户可感知的降级方案？", "hard", ["工程化", "稳定性"]),
    ("请解释 MCP 工具和普通函数调用的区别，以及你会如何做工具权限控制。", "medium", ["MCP", "工具调用"]),
    ("如何评估一个招聘问答 RAG 系统是否真正减少了幻觉？", "hard", ["评测", "RAG"]),
]


async def main() -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(text("UPDATE exams SET title=:title, description=:description, is_active=TRUE WHERE id=:exam AND tenant_id=:tenant"), {"title": "聘达本地测试笔试（大模型应用）", "description": "仅用于联调的 Python/RAG/LangGraph 试卷", "exam": EXAM_ID, "tenant": TENANT})
            for (no, qtype, content, correct, score, tag), qid in zip(QUESTIONS, QUESTION_IDS):
                await session.execute(text("""INSERT INTO questions (id, tenant_id, exam_id, question_no, question_type, content, correct_answer, score, knowledge_tag)
                    VALUES (:id,:tenant,:exam,:no,:type,:content,:correct,:score,:tag)
                    ON CONFLICT (id) DO UPDATE SET question_no=EXCLUDED.question_no,
                        question_type=EXCLUDED.question_type, content=EXCLUDED.content,
                        correct_answer=EXCLUDED.correct_answer, score=EXCLUDED.score,
                        knowledge_tag=EXCLUDED.knowledge_tag"""), {"id": qid, "tenant": TENANT, "exam": EXAM_ID, "no": no, "type": qtype, "content": content, "correct": correct, "score": score, "tag": tag})
            await session.execute(text("DELETE FROM scoring_points WHERE question_id=:qid"), {"qid": QUESTION_IDS[3]})
            for desc, points in [("说明超时控制，避免请求无限等待", 3), ("说明有限次数重试和退避", 3), ("说明切换备用模型或返回可理解的兜底响应", 4)]:
                await session.execute(text("INSERT INTO scoring_points (question_id, point_desc, point_score, is_active) VALUES (:qid,:desc,:points,TRUE)"), {"qid": QUESTION_IDS[3], "desc": desc, "points": points})
            await session.execute(text("""UPDATE job_positions SET title=:title, jd_text=:jd, requirements=:requirements, exam_id=:exam, status='open', updated_at=NOW() WHERE id=:position AND tenant_id=:tenant"""), {"title": TARGET_POSITION, "jd": "负责 RAG、多智能体编排和招聘系统工程化落地；使用 Python、LangGraph、Milvus 和 FastAPI 构建可靠的大模型应用。", "requirements": json.dumps({"skills": ["Python", "RAG", "LangGraph", "Milvus", "FastAPI"]}, ensure_ascii=False), "exam": EXAM_ID, "position": POSITION_ID, "tenant": TENANT})
            await session.execute(text("DELETE FROM interview_questions WHERE tenant_id=:tenant AND target_position=:position"), {"tenant": TENANT, "position": TARGET_POSITION})
            for content, difficulty, tags in INTERVIEW_QUESTIONS:
                await session.execute(text("""INSERT INTO interview_questions (tenant_id, content, difficulty, tags, target_position, is_active)
                    VALUES (:tenant,:content,:difficulty,:tags,:position,TRUE)"""), {"tenant": TENANT, "content": content, "difficulty": difficulty, "tags": json.dumps(tags, ensure_ascii=False), "position": TARGET_POSITION})
    print(f"本地测试夹具已就绪：岗位={POSITION_ID}，笔试题={len(QUESTIONS)}，面试题={len(INTERVIEW_QUESTIONS)}")


if __name__ == "__main__":
    asyncio.run(main())
