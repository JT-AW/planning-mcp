"""FastAPI app, HTTP routes, uvicorn management, and static file serving."""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from planning_mcp.models import AcceptRequest, FeedbackItem, FeedbackRequest, Reply, ReplyRequest
from planning_mcp.reanchor import serialize_feedback, serialize_reply
from planning_mcp.state import broadcast, find_free_port, state

STATIC_DIR = Path(__file__).parent / "static"

api = FastAPI(title="Planning MCP UI", docs_url=None, redoc_url=None)
api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_server_thread: threading.Thread | None = None
_server_port: int | None = None
_uvicorn_server: uvicorn.Server | None = None


@api.get("/")
def get_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@api.get("/plan")
def get_plan() -> JSONResponse:
    with state.lock:
        return JSONResponse(
            {
                "markdown": state.markdown,
                "title": state.title,
            }
        )


@api.get("/feedback/all")
def get_all_feedback() -> JSONResponse:
    """Return all feedback items with replies (for page load/refresh)."""
    with state.lock:
        return JSONResponse([serialize_feedback(f) for f in state.feedback])


@api.post("/feedback")
def submit_feedback(body: FeedbackRequest) -> JSONResponse:
    item = FeedbackItem(
        id=str(uuid.uuid4()),
        type=body.type,
        selected_text=body.selected_text,
        anchor_context=body.anchor_context,
        user_message=body.user_message,
        timestamp=datetime.now(UTC).isoformat(),
        text_offset=body.text_offset,
    )
    with state.lock:
        state.feedback.append(item)
    return JSONResponse({"id": item.id})


@api.post("/feedback/batch")
def submit_feedback_batch(items: list[FeedbackRequest]) -> JSONResponse:
    """Submit multiple feedback items at once."""
    ids: list[str] = []
    with state.lock:
        for body in items:
            item = FeedbackItem(
                id=str(uuid.uuid4()),
                type=body.type,
                selected_text=body.selected_text,
                anchor_context=body.anchor_context,
                user_message=body.user_message,
                timestamp=datetime.now(UTC).isoformat(),
                text_offset=body.text_offset,
            )
            state.feedback.append(item)
            ids.append(item.id)
    return JSONResponse({"ids": ids})


@api.post("/feedback/submit-all")
def submit_all_drafts() -> JSONResponse:
    """Transition all draft feedback items to submitted status."""
    count = 0
    with state.lock:
        for item in state.feedback:
            if item.status == "draft":
                item.status = "submitted"
                count += 1
    return JSONResponse({"submitted": count})


@api.post("/feedback/{feedback_id}/reply")
def add_reply(feedback_id: str, body: ReplyRequest) -> JSONResponse:
    """Add a reply to a feedback thread (from browser)."""
    pushback_type = body.pushback_type
    if pushback_type == "none" and body.is_pushback:
        pushback_type = "disagree"
    reply = Reply(
        id=str(uuid.uuid4()),
        feedback_id=feedback_id,
        author="user",
        message=body.message,
        timestamp=datetime.now(UTC).isoformat(),
        pushback_type=pushback_type,
        pushback_reasoning=body.pushback_reasoning,
    )
    found = False
    with state.lock:
        for item in state.feedback:
            if item.id == feedback_id:
                item.replies.append(reply)
                found = True
                break
    if found:
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": serialize_reply(reply)})
        return JSONResponse({"id": reply.id})
    return JSONResponse({"error": "not found"}, status_code=404)


@api.post("/accept")
def accept_plan_endpoint(body: AcceptRequest) -> JSONResponse:
    """Accept the current plan — writes to the specified path."""
    from planning_mcp.tools import accept_plan

    result = accept_plan(body.save_path)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@api.get("/events")
def sse_stream() -> StreamingResponse:
    def generate() -> Generator[str]:
        q: queue.SimpleQueue[str] = queue.SimpleQueue()
        with state.lock:
            state.sse_subscribers.append(q)
        try:
            yield 'data: {"type": "connected"}\n\n'
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with state.lock:
                if q in state.sse_subscribers:
                    state.sse_subscribers.remove(q)

    return StreamingResponse(generate(), media_type="text/event-stream")


def start_web_server() -> int:
    global _server_thread, _server_port, _uvicorn_server

    if _server_thread is not None and _server_thread.is_alive():
        assert _server_port is not None
        return _server_port

    port = find_free_port()
    _server_port = port

    config = uvicorn.Config(app=api, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    _uvicorn_server = server

    _server_thread = threading.Thread(target=server.run, daemon=True, name="planning-mcp-web")
    _server_thread.start()

    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    return port
