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

from planning_mcp import db
from planning_mcp.models import AcceptRequest, FeedbackItem, FeedbackRequest, Reply, ReplyRequest
from planning_mcp.reanchor import serialize_feedback, serialize_reply
from planning_mcp.state import broadcast, find_free_port, state
from planning_mcp.vault import accept_plan_to_vault

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
    from planning_mcp import state as st

    plan_status = None
    if st.current_plan_id:
        db.init_db()
        plan = db.get_plan(st.current_plan_id)
        if plan:
            plan_status = plan["status"]

    with state.lock:
        return JSONResponse(
            {
                "markdown": state.markdown,
                "title": state.title,
                "plan_id": st.current_plan_id,
                "project_id": st.current_project_id,
                "status": plan_status,
            }
        )


@api.get("/feedback/all")
def get_all_feedback() -> JSONResponse:
    """Return all feedback items with replies (for page load/refresh)."""
    with state.lock:
        return JSONResponse([serialize_feedback(f) for f in state.feedback])


@api.post("/feedback")
def submit_feedback(body: FeedbackRequest) -> JSONResponse:
    from planning_mcp import state as st

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
    # Dual-write to DB
    if st.current_plan_id:
        db.create_feedback(
            item.id,
            st.current_plan_id,
            item.type,
            item.selected_text,
            item.anchor_context,
            item.user_message,
            item.text_offset,
        )
    return JSONResponse({"id": item.id})


@api.post("/feedback/batch")
def submit_feedback_batch(items: list[FeedbackRequest]) -> JSONResponse:
    """Submit multiple feedback items at once."""
    from planning_mcp import state as st

    created: list[FeedbackItem] = []
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
            created.append(item)
            ids.append(item.id)
    # Dual-write to DB
    if st.current_plan_id:
        for item in created:
            db.create_feedback(
                item.id,
                st.current_plan_id,
                item.type,
                item.selected_text,
                item.anchor_context,
                item.user_message,
                item.text_offset,
            )
    return JSONResponse({"ids": ids})


@api.post("/feedback/submit-all")
def submit_all_drafts() -> JSONResponse:
    """Transition all draft feedback items to submitted status."""
    from planning_mcp import state as st

    count = 0
    submitted_ids: list[str] = []
    with state.lock:
        for item in state.feedback:
            if item.status == "draft":
                item.status = "submitted"
                submitted_ids.append(item.id)
                count += 1
    # Dual-write to DB
    if st.current_plan_id:
        for fid in submitted_ids:
            db.update_feedback_status(fid, "submitted")
    return JSONResponse({"submitted": count})


@api.post("/feedback/{feedback_id}/reply")
def add_reply(feedback_id: str, body: ReplyRequest) -> JSONResponse:
    """Add a reply to a feedback thread (from browser)."""
    # Resolve pushback_type: explicit field wins, fall back to is_pushback compat
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
        # Dual-write reply to DB
        from planning_mcp import state as st

        if st.current_plan_id:
            db.create_reply(
                reply.id, feedback_id, "user", body.message, pushback_type, body.pushback_reasoning
            )
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": serialize_reply(reply)})
        return JSONResponse({"id": reply.id})
    return JSONResponse({"error": "not found"}, status_code=404)


# ── DB-backed project/plan endpoints ────────────────────────────────────────


@api.get("/projects")
def list_projects() -> JSONResponse:
    """Return projects as a nested tree for the sidebar."""
    db.init_db()
    flat = db.list_projects()
    # Build tree: group by parent_id, then nest
    by_parent: dict[str | None, list[dict]] = {}  # type: ignore[type-arg]
    for p in flat:
        pid = p.get("parent_id")
        by_parent.setdefault(pid, []).append(p)

    def build_tree(parent_id: str | None) -> list[dict]:  # type: ignore[type-arg]
        children = by_parent.get(parent_id, [])
        for c in children:
            c["children"] = build_tree(c["id"])
        return children

    return JSONResponse(build_tree(None))


@api.get("/projects/{project_id}")
def get_project(project_id: str) -> JSONResponse:
    db.init_db()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(project)


@api.get("/projects/{project_id}/plans")
def get_project_plans(project_id: str) -> JSONResponse:
    db.init_db()
    plans = db.list_plans(project_id)
    return JSONResponse(plans)


@api.get("/plans/{plan_id}")
def get_plan_by_id(plan_id: str) -> JSONResponse:
    db.init_db()
    plan = db.get_plan(plan_id)
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)
    return JSONResponse(plan)


@api.post("/plans/{plan_id}/accept")
def accept_plan_endpoint(plan_id: str, body: AcceptRequest) -> JSONResponse:
    """Accept a plan — writes to vault, updates DB status."""
    db.init_db()
    plan = db.get_plan(plan_id)
    if not plan:
        return JSONResponse({"error": "Plan not found"}, status_code=404)

    project = db.get_project(plan["project_id"])
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    vault_path = accept_plan_to_vault(
        project,
        plan,
        vault_domain=body.vault_domain or None,
        vault_filename=body.vault_filename or None,
    )
    db.update_plan_status(plan_id, "accepted", vault_path=vault_path)

    return JSONResponse({"ok": True, "vault_path": vault_path})


@api.post("/projects/{project_id}/plans/new-cycle")
def new_plan_cycle(project_id: str) -> JSONResponse:
    """Reject the current plan and start a new cycle."""
    db.init_db()
    project = db.get_project(project_id)
    if not project:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    # Reject the latest non-accepted plan
    plans = db.list_plans(project_id)
    for p in reversed(plans):
        if p["status"] in ("draft", "reviewing"):
            db.update_plan_status(p["id"], "rejected")
            break

    cycle = db.get_next_cycle_number(project_id)
    return JSONResponse({"project_id": project_id, "next_cycle_number": cycle})


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
