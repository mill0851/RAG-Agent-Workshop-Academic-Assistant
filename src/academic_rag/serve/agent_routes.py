"""The answering agent's routes: the Ask page's questions, its model choices, the MCP
endpoint that Claude Code and Codex call our tools through, and the deadlines.

    POST /ask              a question in, the answer streamed back as JSON lines
    POST /mcp/{session}    the tools of one question in progress, for Claude Code and Codex
    GET  /models           the AIs the Ask page offers
    GET  /due              what is due soon (the Courses page's panel)
"""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from .. import agent, course_files, index
from ..config import Settings
from ..llm import ProviderError
from ..tools import Toolbox, due_soon, mcp
from .jobs import clean_error
from .state import AskRequest, State


def agent_router(settings: Settings, state: State) -> APIRouter:
    router = APIRouter()

    @router.get("/models")
    def models() -> dict:
        """The AIs the Ask page can offer: every provider and model, and whether each works here."""
        return {"default": {"provider": settings.llm_provider or None, "model": settings.llm_model,
                            "effort": settings.reasoning_level},
                "choices": state.models()}

    @router.post("/ask")
    def ask(req: AskRequest, request: Request):
        """Answer a question from your course material (the Ask tab). The answer streams back
        as JSON lines: the sources, the tools it calls, pieces of text, a trace, then done."""
        try:
            provider = state.provider(req.provider, req.model)
        except ProviderError as exc:
            raise HTTPException(400, str(exc))
        course_id = None
        if req.course:
            course_id, problem = index.resolve_course(course_files.list_courses(settings), req.course)
            if problem:
                raise HTTPException(404, problem)

        # This question's tools, reachable for Claude Code and Codex at a URL nobody can guess
        box = Toolbox(settings, state.store(), state.embedder(), course_id=course_id)
        session = secrets.token_urlsafe(16)
        box.mcp_url = f"{str(request.base_url).rstrip('/')}/mcp/{session}"
        state.sessions[session] = box
        events = agent.ask(req.question, box, provider, history=[t.model_dump() for t in req.history],
                           effort=req.effort)  # None: REASONING_LEVEL from .env

        def lines():
            try:
                for event in events:
                    yield json.dumps(event) + "\n"
            except ProviderError as exc:  # not installed, not logged in, bad key, ...
                yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
            except Exception as exc:
                yield json.dumps({"type": "error", "message": clean_error(exc)}) + "\n"
            finally:
                state.sessions.pop(session, None)  # the question is over: its tools close

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @router.post("/mcp/{session}")
    def mcp_endpoint(session: str, message: dict = Body(...)):
        """MCP over HTTP (tools/mcp.py): how Claude Code and Codex call the tools of the
        question that is being answered."""
        box = state.sessions.get(session)
        if box is None:
            raise HTTPException(404, "No question in progress with this session.")
        reply = mcp.handle(message, box)
        return Response(status_code=202) if reply is None else reply

    @router.get("/mcp/{session}")
    def mcp_stream(session: str):
        """Agents may ask for a stream of server messages; this server sends none."""
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.get("/due")
    def due(days: int = Query(7, ge=1, le=60, description="How many days ahead to look")) -> dict:
        """Assignments and quizzes due in the next `days` days, in your time zone."""
        return {"days": days, "items": due_soon(settings, days)}

    return router
