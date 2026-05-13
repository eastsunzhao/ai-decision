import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.scenario import ScenarioManager
from app.core.session_store import SessionStore
from app.core.state import ResearchState
from app.graph.workflow import AgentWorkflow
from app.schemas.models import ChatRequest, ChatResponse

router = APIRouter()
logger = logging.getLogger("ai_decision.api.chat")

scenario_manager = ScenarioManager()
workflow = AgentWorkflow(scenario_manager)
session_store = SessionStore()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    entry_id = request.entry_id
    logger.info("chat_request_received analyst_id=%s query_len=%s", entry_id, len(request.query))
    if not scenario_manager.has_scenario(entry_id):
        logger.warning("chat_analyst_not_found analyst_id=%s", entry_id)
        raise HTTPException(status_code=404, detail=f"AI analyst {entry_id} not found")

    final_state = workflow.run(
        ResearchState(
            scenario_id=entry_id,
            query=request.query,
        )
    )

    if not final_state.answer:
        logger.error("chat_answer_empty analyst_id=%s errors=%s", entry_id, final_state.errors)
        raise HTTPException(status_code=500, detail="Failed to generate answer")

    logger.info(
        "chat_request_completed analyst_id=%s citations=%s confidence=%.2f reflection_iterations=%s",
        entry_id,
        len(final_state.citations),
        final_state.confidence or 0.0,
        final_state.reflection_iterations,
    )
    response = _to_chat_response(final_state)
    session = session_store.save_session(entry_id, request.query, response.model_dump())
    response.session_id = session["session_id"]
    return response


@router.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    entry_id = request.entry_id
    logger.info("chat_stream_request_received analyst_id=%s query_len=%s", entry_id, len(request.query))
    if not scenario_manager.has_scenario(entry_id):
        logger.warning("chat_stream_analyst_not_found analyst_id=%s", entry_id)
        raise HTTPException(status_code=404, detail=f"AI analyst {entry_id} not found")

    def event_stream():
        state = ResearchState(scenario_id=entry_id, query=request.query)
        for event in workflow.run_with_events(state):
            if event.get("type") == "final":
                response = _to_chat_response(event["data"])
                session = session_store.save_session(entry_id, request.query, response.model_dump())
                response.session_id = session["session_id"]
                payload = response.model_dump()
                logger.info(
                    "chat_stream_final analyst_id=%s citations=%s confidence=%.2f",
                    entry_id,
                    len(payload.get("citations") or []),
                    payload.get("confidence") or 0.0,
                )
                yield _sse({"type": "final", "data": payload})
            else:
                logger.info("chat_stream_step analyst_id=%s node=%s status=%s", entry_id, event.get("name"), event.get("status"))
                yield _sse(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _to_chat_response(final_state: ResearchState) -> ChatResponse:
    return ChatResponse(
        session_id=None,
        answer=final_state.answer,
        answer_sections=final_state.answer_sections,
        citations=final_state.citations,
        citation_details=final_state.citation_details,
        modules_used=final_state.modules_used,
        skills_used=final_state.skills_used,
        confidence=final_state.confidence or 0.0,
        follow_up_questions=final_state.follow_up_questions,
        task_plan=final_state.task_plan,
        execution_steps=final_state.execution_steps,
        reflection={
            "iterations": final_state.reflection_iterations,
            "related_entities": final_state.related_entities,
            "evidence_coverage": final_state.evidence_coverage,
            "trace": final_state.reflection_trace,
        },
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


@router.get("/scenarios")
def scenarios() -> dict:
    logger.info("scenarios_requested")
    return {"scenarios": scenario_manager.list_scenarios()}


@router.get("/analysts")
def analysts() -> dict:
    logger.info("analysts_requested")
    return {"analysts": scenario_manager.list_analysts()}


@router.get("/sessions")
def sessions() -> dict:
    logger.info("sessions_requested")
    return {"sessions": session_store.list_sessions()}


@router.get("/sessions/{session_id}")
def session_detail(session_id: str) -> dict:
    session = session_store.get_session(session_id)
    if not session:
        logger.warning("session_not_found session_id=%s", session_id)
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    logger.info("session_detail_requested session_id=%s", session_id)
    return session


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    deleted = session_store.delete_session(session_id)
    if not deleted:
        logger.warning("session_delete_not_found session_id=%s", session_id)
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return {"ok": True, "session_id": session_id}
