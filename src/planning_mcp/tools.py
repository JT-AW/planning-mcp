"""FastMCP server and MCP tool definitions."""

from __future__ import annotations

import threading
import uuid
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from planning_mcp import db
from planning_mcp.models import Reply
from planning_mcp.reanchor import _reanchor_all_comments, serialize_feedback, serialize_reply
from planning_mcp.sections import replace_section_body
from planning_mcp.state import broadcast, state
from planning_mcp.vault import accept_plan_to_vault
from planning_mcp.web import start_web_server

mcp = FastMCP(
    name="planning-mcp",
    instructions=(
        "Interactive plan review tool. Publish a plan with open_plan, "
        "then poll get_feedback for user annotations. Use update_section "
        "to push revised content back to the browser. Use reply_to_feedback "
        "to respond to specific comments in-thread.\n\n"
        "IMPORTANT: When calling open_plan or update_plan, prefer writing the "
        "markdown to a temporary file and passing plan_file instead of inlining "
        "the full content in plan_markdown. This avoids bloating the tool call "
        "payload. Example: write to /tmp/plan.md, then call "
        'open_plan(plan_file="/tmp/plan.md", plan_title="My Plan").\n\n'
        "Project-aware mode: pass project_id to open_plan to persist plans in "
        "SQLite. Use accept_plan to finalize and write to the vault. "
        "Use get_plan_history to view past plan cycles for a project."
    ),
)


def _persist_plan_markdown(markdown: str) -> None:
    """Dual-write: update SQLite if a plan is active."""
    from planning_mcp import state as st

    if st.current_plan_id:
        db.update_plan_markdown(st.current_plan_id, markdown)


@mcp.tool()
def open_plan(
    plan_markdown: str = "",
    plan_file: str = "",
    plan_title: str = "Plan Review",
    project_id: str = "",
) -> dict[str, object]:
    """Publish a plan to the browser for interactive review.

    PREFERRED: Write markdown to a temp file and pass plan_file="/tmp/plan.md"
    instead of inlining content in plan_markdown. This keeps the tool call small.

    If both are given, plan_file takes precedence.
    Pass project_id to enable persistent plan tracking across sessions.
    Starts the web server on first call (subsequent calls reuse it).
    Opens the browser automatically. Returns {"port": int, "url": str}.
    """
    from planning_mcp import state as st

    markdown = plan_markdown
    if plan_file:
        path = Path(plan_file).expanduser()
        markdown = path.read_text(encoding="utf-8")

    if not markdown:
        return {"error": "Provide either plan_markdown or plan_file"}

    port = start_web_server()
    url = f"http://127.0.0.1:{port}"

    with state.lock:
        state.markdown = markdown
        state.title = plan_title
        state.feedback.clear()

    # DB persistence
    db.init_db()
    plan_id = str(uuid.uuid4())

    if project_id:
        st.current_project_id = project_id
    else:
        # Ad-hoc project for backward compat
        project_id = str(uuid.uuid4())
        st.current_project_id = project_id
        db.create_adhoc_project(project_id, f"Ad-hoc: {plan_title}")

    cycle = db.get_next_cycle_number(project_id)
    db.create_plan(plan_id, project_id, cycle, plan_title, markdown)
    st.current_plan_id = plan_id

    broadcast("plan_updated")
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

    return {"port": port, "url": url, "plan_id": plan_id, "project_id": project_id}


@mcp.tool()
def get_feedback() -> list[dict[str, object]]:
    """Return all submitted (not draft/processed) feedback items from the browser.

    Each item: id, type ("investigate" | "update_opinion" | "overall"),
    selected_text (the highlighted text), anchor_context (surrounding text
    for re-anchoring), user_message, timestamp, replies[].
    """
    with state.lock:
        return [serialize_feedback(f) for f in state.feedback if f.status == "submitted"]


@mcp.tool()
def mark_feedback_processed(feedback_id: str) -> dict[str, object]:
    """Mark a feedback item as handled so it won't reappear in get_feedback."""
    from planning_mcp import state as st

    with state.lock:
        for item in state.feedback:
            if item.id == feedback_id:
                item.status = "processed"
                # Dual-write to DB
                if st.current_plan_id:
                    db.update_feedback_status(feedback_id, "processed")
                return {"ok": True}
    return {"ok": False, "error": f"Feedback item {feedback_id!r} not found"}


@mcp.tool()
def update_plan(plan_markdown: str = "", plan_file: str = "") -> dict[str, object]:
    """Replace the entire plan with updated markdown. Browser auto-refreshes via SSE.

    PREFERRED: Write markdown to a temp file and pass plan_file="/tmp/plan.md"
    instead of inlining content in plan_markdown. This keeps the tool call small.
    """
    markdown = plan_markdown
    if plan_file:
        path = Path(plan_file).expanduser()
        markdown = path.read_text(encoding="utf-8")

    if not markdown:
        return {"error": "Provide either plan_markdown or plan_file"}

    with state.lock:
        state.markdown = markdown
        comment_state = _reanchor_all_comments(markdown, state.feedback)

    _persist_plan_markdown(markdown)
    broadcast("plan_updated", {"comments": comment_state})
    return {"ok": True}


@mcp.tool()
def update_section(
    section_title: str,
    new_content: str,
) -> dict[str, object]:
    """Update a single section of the plan by title.

    Returns {"ok": bool, "warning": str | None}.
    """
    with state.lock:
        updated, warning = replace_section_body(state.markdown, section_title, new_content)
        if warning is None or "not found" not in warning:
            state.markdown = updated
            comment_state = _reanchor_all_comments(updated, state.feedback)
        else:
            comment_state = []

    found = warning is None or "not found" not in warning
    if found:
        _persist_plan_markdown(updated)
        broadcast("plan_updated", {"section": section_title, "comments": comment_state})
    return {"ok": found, "warning": warning}


@mcp.tool()
def reply_to_feedback(
    feedback_id: str,
    message: str,
    pushback_type: str = "none",
    pushback_reasoning: str | None = None,
) -> dict[str, object]:
    """Reply to a user's feedback comment. Appears as a threaded reply in the margin.

    pushback_type can be "none", "disagree", or "alternative".
    If "disagree" or "alternative", provide pushback_reasoning for context.
    The reply gets distinct visual treatment in the browser.
    """
    from planning_mcp import state as st

    reply = Reply(
        id=str(uuid.uuid4()),
        feedback_id=feedback_id,
        author="claude",
        message=message,
        timestamp=datetime.now(UTC).isoformat(),
        pushback_type=pushback_type,  # type: ignore[arg-type]
        pushback_reasoning=pushback_reasoning,
    )
    found = False
    with state.lock:
        for item in state.feedback:
            if item.id == feedback_id:
                item.replies.append(reply)
                found = True
                break
    if found:
        # Dual-write to DB
        if st.current_plan_id:
            db.create_reply(
                reply.id, feedback_id, "claude", message, pushback_type, pushback_reasoning
            )
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": serialize_reply(reply)})
        return {"ok": True, "reply_id": reply.id}
    return {"ok": False, "error": f"Feedback {feedback_id!r} not found"}


@mcp.tool()
def accept_plan(
    vault_domain: str = "",
    vault_filename: str = "",
) -> dict[str, object]:
    """Accept the current plan, writing it to the Obsidian vault.

    Optional: vault_domain overrides the domain folder name.
    Optional: vault_filename overrides the output filename.
    """
    from planning_mcp import state as st

    if not st.current_plan_id:
        return {"error": "No active plan to accept"}

    plan = db.get_plan(st.current_plan_id)
    if not plan:
        return {"error": "Plan not found in database"}

    project = db.get_project(plan["project_id"])
    if not project:
        return {"error": "Project not found in database"}

    vault_path = accept_plan_to_vault(
        project,
        plan,
        vault_domain=vault_domain or None,
        vault_filename=vault_filename or None,
    )

    db.update_plan_status(st.current_plan_id, "accepted", vault_path=vault_path)

    return {"ok": True, "vault_path": vault_path, "plan_id": st.current_plan_id}


@mcp.tool()
def get_plan_history(project_id: str = "") -> list[dict[str, object]]:
    """Return all plan cycles for a project. If project_id is empty, uses current project."""
    from planning_mcp import state as st

    pid = project_id or st.current_project_id
    if not pid:
        return []

    plans = db.list_plans(pid)
    return [
        {
            "id": p["id"],
            "cycle_number": p["cycle_number"],
            "title": p["title"],
            "status": p["status"],
            "created_at": p["created_at"],
            "accepted_at": p["accepted_at"],
            "vault_path": p["vault_path"],
        }
        for p in plans
    ]
