from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from backend.agents.qa.graph import _route_by_confidence, _route_by_query_type
from backend.agents.qa.nodes import (
    _candidate_safe_answer,
    _classify_question_scope,
    _rule_classify_specialized,
    generate_direct_node,
    generate_general_node,
    retrieve_node,
    web_search_node,
)
from backend.agents.qa.prompts import RAG_ANSWER_PROMPT, SYSTEM_PROMPT


def _state(**overrides):
    state = {
        "messages": [HumanMessage(content="测试问题")],
        "student_id": "candidate-1",
        "tenant_id": "tenant-1",
        "session_id": "session-1",
        "application_id": None,
        "position_id": "position-1",
        "candidate_task_context": None,
        "original_query": "测试问题",
        "query_type": "PRECISE",
        "question_scope": "general",
        "rewritten_queries": [],
        "hyde_document": None,
        "ranked_chunks": [],
        "confidence": 0.0,
        "is_high_confidence": False,
        "web_search_results": [],
        "enable_web_search": False,
    }
    state.update(overrides)
    return state


def test_recruitment_keywords_bypass_generic_classifier():
    assert _rule_classify_specialized("这个岗位要求几年工作经验？")
    assert _classify_question_scope("公司的公积金比例是多少？") == "recruitment"


def test_private_results_and_application_state_are_separate_scopes():
    assert _classify_question_scope("我的面试成绩和排名是多少？") == "private_result"
    assert _classify_question_scope("我的申请进度到哪一步了？") == "application_state"


@pytest.mark.asyncio
async def test_recruitment_low_confidence_never_calls_llm_or_web(monkeypatch):
    monkeypatch.setattr("backend.agents.qa.nodes.get_llm", AsyncMock(side_effect=AssertionError))
    state = _state(
        original_query="这个岗位的薪资是多少？",
        question_scope="recruitment",
        enable_web_search=True,
    )
    assert await web_search_node(state) == {"web_search_results": []}
    result = await generate_direct_node(state)
    assert result["answer_mode"] == "restricted"
    assert "联系 HR" in result["answer"]


@pytest.mark.asyncio
async def test_private_result_answer_does_not_call_llm(monkeypatch):
    monkeypatch.setattr("backend.agents.qa.nodes.get_llm", AsyncMock(side_effect=AssertionError))
    result = await generate_general_node(_state(question_scope="private_result"))
    assert result["answer_mode"] == "controlled"
    assert "评分" in result["answer"]
    assert "HR" in result["answer"]


def test_candidate_task_answer_contains_only_safe_task_fields():
    answer = _candidate_safe_answer(_state(
        question_scope="application_state",
        candidate_task_context={
            "position_title": "大模型应用开发工程师",
            "tasks": [{
                "type": "exam", "available": True, "completed": False,
                "deadline": "2026-09-05T10:00:00+00:00",
            }],
        },
    ))
    assert "在线笔试：可进行" in answer
    assert "评分和招聘结论不会" in answer


@pytest.mark.asyncio
async def test_position_id_maps_to_legacy_retrieval_filter(monkeypatch):
    seen = {}

    def fake_retrieve(query, tenant_id, course_id, **kwargs):
        seen.update(query=query, tenant_id=tenant_id, course_id=course_id)
        return [], 0.0

    monkeypatch.setattr("backend.core.reranker.retrieve", fake_retrieve)
    await retrieve_node(_state(original_query="岗位职责是什么？"))
    assert seen == {
        "query": "岗位职责是什么？",
        "tenant_id": "tenant-1",
        "course_id": "position-1",
    }


def test_graph_routes_are_preserved_and_prompts_enforce_boundaries():
    assert _route_by_query_type({"query_type": "VAGUE"}) == "VAGUE"
    assert _route_by_confidence({"is_high_confidence": True}) == "high"
    assert "严格基于参考内容" in RAG_ANSWER_PROMPT
    assert "不披露评分、排名、评审报告" in RAG_ANSWER_PROMPT
    assert "不承诺录用" in SYSTEM_PROMPT
