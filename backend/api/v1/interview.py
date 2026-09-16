import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import text

from backend.agents.interview.graph import build_interview_graph
from backend.agents.interview.state import InterviewStage
from backend.core.application_service import (
    ApplicationAction,
    ApplicationService,
)
from backend.core.exceptions import (
    ApplicationNotFoundError,
    ApplicationPermissionError,
    ApplicationStateError,
)
from backend.core.logger import get_logger
from backend.core.memory import build_config, build_thread_id
from backend.dependencies import AsyncSessionLocal, get_current_user

router = APIRouter()
logger = get_logger(__name__)
_graph = build_interview_graph()
RECRUITER_ROLES = {"teacher", "admin", "hr", "recruiter"}


class StartSessionRequest(BaseModel):
    application_id: uuid.UUID


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _require_candidate(current_user: dict) -> None:
    if current_user.get("role") != "student":
        raise HTTPException(status_code=403, detail="仅候选人可以参加 AI 初面")


def _raise_application_http_error(exc: Exception) -> None:
    if isinstance(exc, ApplicationNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ApplicationPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ApplicationStateError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _candidate_finished_payload(session_id: str, finished: bool) -> dict:
    return {
        "session_id": session_id,
        "status": "submitted" if finished else "in_progress",
        "message": "AI 初面已完成，请等待 HR 通知" if finished else "AI 初面进行中",
    }


async def _recover_start_failure(
    *, application_id: str, candidate_id: str, tenant_id: str, session_id: str
) -> None:
    async with AsyncSessionLocal() as session:
        try:
            await ApplicationService(session).reset_interview_start_after_failure(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                session_id=session_id,
            )
            await session.execute(
                text("""
                    DELETE FROM interview_sessions
                    WHERE session_id=:session_id AND tenant_id=:tenant_id
                      AND student_id=:candidate_id AND status='in_progress'
                """),
                {"session_id": session_id, "tenant_id": tenant_id,
                 "candidate_id": candidate_id},
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("interview.start_recovery_failed", session_id=session_id, error=str(exc))


async def _assert_candidate_session(
    *, session_id: str, current_user: dict
) -> str:
    """校验会话归属、申请绑定、任务状态和面试时间窗口。"""
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text("""
                    SELECT a.id AS application_id
                    FROM interview_sessions i
                    JOIN recruitment_applications a ON a.interview_session_id = i.id
                    WHERE i.session_id=:session_id
                      AND i.tenant_id=:tenant_id
                      AND i.student_id=:candidate_id
                      AND i.status='in_progress'
                """),
                {"session_id": session_id, "tenant_id": current_user["tenant_id"],
                 "candidate_id": current_user["user_id"]},
            )
        ).mappings().fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="进行中的面试会话不存在")
        try:
            await ApplicationService(session).assert_action_allowed(
                application_id=str(row["application_id"]),
                candidate_id=current_user["user_id"],
                tenant_id=current_user["tenant_id"],
                action=ApplicationAction.INTERVIEW_MESSAGE,
            )
        except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
            _raise_application_http_error(exc)
        return str(row["application_id"])


@router.post("/sessions", status_code=201)
async def start_session(
    req: StartSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_candidate(current_user)
    application_id = str(req.application_id)
    candidate_id = current_user["user_id"]
    tenant_id = current_user["tenant_id"]
    session_id = str(uuid.uuid4())
    thread_id = build_thread_id(candidate_id, session_id)

    try:
        async with AsyncSessionLocal() as session:
            service = ApplicationService(session)
            context = await service.get_interview_position_context(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                for_update=True,
            )
            await service.start_interview(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
            )
            await session.execute(
                text("""
                    INSERT INTO interview_sessions
                        (id, tenant_id, student_id, session_id, thread_id,
                         target_position, resume_review_id, status)
                    VALUES (:id, :tenant_id, :student_id, :session_id, :thread_id,
                            :position_title, :resume_review_id, 'in_progress')
                """),
                {"id": str(uuid.uuid4()), "tenant_id": tenant_id,
                 "student_id": candidate_id, "session_id": session_id,
                 "thread_id": thread_id, "position_title": context.position_title,
                 "resume_review_id": context.resume_review_id},
            )
            await service.attach_interview_session(
                application_id=application_id,
                candidate_id=candidate_id,
                tenant_id=tenant_id,
                session_id=session_id,
            )
            await session.commit()
    except (ApplicationNotFoundError, ApplicationPermissionError, ApplicationStateError) as exc:
        _raise_application_http_error(exc)

    initial_state = {
        "messages": [HumanMessage(content="[开始面试]")],
        "student_id": candidate_id, "tenant_id": tenant_id,
        "session_id": session_id, "application_id": context.application_id,
        "position_id": context.position_id, "target_position": context.position_title,
        "job_description": context.job_description,
        "job_requirements": context.job_requirements,
        "resume_review_id": context.resume_review_id,
        "resume_projects": [], "resume_skills": [],
        "current_stage": InterviewStage.WARMUP.value, "stage_turn_count": 0,
        "total_turn_count": 0, "max_turns": 40, "question_bank": [],
        "current_question": None, "projects_asked": [],
        "last_answer_quality": "adequate", "followup_count": 0,
        "existing_summary": None, "should_summarize": False,
        "report": None, "fallback_used": False, "structured_output": None,
    }
    try:
        result = await _graph.ainvoke(
            initial_state, config=build_config(candidate_id, session_id, "interview")
        )
    except Exception as exc:
        await _recover_start_failure(
            application_id=application_id, candidate_id=candidate_id,
            tenant_id=tenant_id, session_id=session_id,
        )
        logger.error("interview.start_failed", session_id=session_id, error=str(exc))
        raise HTTPException(status_code=500, detail="AI 初面启动失败，请重试") from exc

    return {
        "session_id": session_id,
        "target_position": context.position_title,
        "status": "in_progress",
        "message": _get_last_ai_message(result.get("messages", [])),
    }


@router.post("/sessions/{session_id}/chat")
async def chat(
    session_id: uuid.UUID,
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_candidate(current_user)
    sid = str(session_id)
    await _assert_candidate_session(session_id=sid, current_user=current_user)
    update = {
        "messages": [HumanMessage(content=req.message.strip())],
        "student_id": current_user["user_id"], "session_id": sid,
        "tenant_id": current_user["tenant_id"],
    }
    result = await _graph.ainvoke(
        update, config=build_config(current_user["user_id"], sid, "interview")
    )
    stage = result.get("current_stage", InterviewStage.WARMUP.value)
    finished = stage == InterviewStage.FINISHED.value
    return {
        "session_id": sid,
        "reply": _get_last_ai_message(result.get("messages", [])),
        "current_stage": stage,
        "total_turns": result.get("total_turn_count", 0),
        "is_finished": finished,
        "submission": _candidate_finished_payload(sid, finished) if finished else None,
    }


@router.get("/sessions/{session_id}/report")
async def get_report(
    session_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
):
    sid = str(session_id)
    recruiter = current_user.get("role") in RECRUITER_ROLES
    owner_clause = "" if recruiter else " AND i.student_id=:user_id"
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                text(f"""
                    SELECT i.target_position, i.status, i.overall_score, i.report, i.finished_at
                    FROM interview_sessions i
                    WHERE i.session_id=:session_id AND i.tenant_id=:tenant_id {owner_clause}
                """),
                {"session_id": sid, "tenant_id": current_user["tenant_id"],
                 "user_id": current_user["user_id"]},
            )
        ).mappings().fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="面试记录不存在")
    if not recruiter:
        return _candidate_finished_payload(sid, row["status"] == "finished")
    if row["status"] != "finished":
        raise HTTPException(status_code=409, detail="面试尚未完成")
    report = row["report"] if isinstance(row["report"], dict) else json.loads(row["report"] or "{}")
    return {
        "session_id": sid, "target_position": row["target_position"],
        "overall_score": row["overall_score"], "dimensions": report.get("dimensions", []),
        "strengths": report.get("strengths", []), "improvements": report.get("improvements", []),
        "overall_comment": report.get("overall_comment", ""),
        "recommended_topics": report.get("recommended_topics", []),
        "next_step_advice": report.get("next_step_advice", ""),
        "evidence": report.get("evidence", []), "risk_flags": report.get("risk_flags", []),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


@router.get("/sessions")
async def list_sessions(current_user: dict = Depends(get_current_user)):
    _require_candidate(current_user)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text("""
                    SELECT session_id, target_position, status, created_at
                    FROM interview_sessions
                    WHERE student_id=:student_id AND tenant_id=:tenant_id
                    ORDER BY created_at DESC LIMIT 50
                """),
                {"student_id": current_user["user_id"], "tenant_id": current_user["tenant_id"]},
            )
        ).mappings().all()
    return {"items": [
        {**_candidate_finished_payload(row["session_id"], row["status"] == "finished"),
         "target_position": row["target_position"],
         "created_at": row["created_at"].isoformat() if row["created_at"] else None}
        for row in rows
    ], "total": len(rows)}


@router.post("/sessions/{session_id}/chat/stream")
async def chat_stream(
    session_id: uuid.UUID,
    req: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    _require_candidate(current_user)
    sid = str(session_id)
    await _assert_candidate_session(session_id=sid, current_user=current_user)
    config = build_config(current_user["user_id"], sid, "interview")
    update = {
        "messages": [HumanMessage(content=req.message.strip())],
        "student_id": current_user["user_id"], "session_id": sid,
        "tenant_id": current_user["tenant_id"],
    }

    async def event_generator():
        try:
            async for event in _graph.astream_events(update, config=config, version="v2"):
                if (event["event"] == "on_chat_model_stream"
                        and event.get("metadata", {}).get("langgraph_node") == "generate_response"):
                    chunk = event["data"].get("chunk")
                    if chunk and chunk.content:
                        yield {"data": json.dumps(
                            {"type": "token", "content": chunk.content}, ensure_ascii=False
                        )}
            final = await _graph.aget_state(config)
            state = final.values if final else {}
            stage = state.get("current_stage", InterviewStage.WARMUP.value)
            finished = stage == InterviewStage.FINISHED.value
            payload = {
                "type": "done", "reply": _get_last_ai_message(state.get("messages", [])),
                "current_stage": stage, "total_turns": state.get("total_turn_count", 0),
                "is_finished": finished,
            }
            if finished:
                payload["submission"] = _candidate_finished_payload(sid, True)
            yield {"data": json.dumps(payload, ensure_ascii=False)}
        except Exception as exc:
            logger.error("interview.chat_stream_error", session_id=sid, error=str(exc))
            yield {"data": json.dumps(
                {"type": "error", "message": "面试响应异常，请重试"}, ensure_ascii=False
            )}

    return EventSourceResponse(event_generator())


def _get_last_ai_message(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            if isinstance(message.content, list):
                return "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in message.content
                )
            return str(message.content)
    return "面试已开始，请等待面试官回应"
