"""FastMCP server and MCP tool definitions."""

from __future__ import annotations

import uuid
import webbrowser
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from planning_mcp.models import Reply
from planning_mcp.reanchor import _reanchor_all_comments, serialize_feedback, serialize_reply
from planning_mcp.sections import replace_section_body
from planning_mcp.state import broadcast, state
from planning_mcp.web import start_web_server

mcp = FastMCP(
    name="planning-mcp",
    instructions=(
        "Interactive plan review tool. Publish a plan with open_plan, "
        "then poll get_feedback for user annotations. Use update_section "
        "to push revised content back to the browser. Use reply_to_feedback "
        "to respond to specific comments in-thread."
    ),
)


@mcp.tool()
def open_plan(plan_markdown: str, plan_title: str = "Plan Review") -> dict[str, object]:
    """Publish a plan to the browser for interactive review.

    Starts the web server on first call (subsequent calls reuse it).
    Opens the browser automatically. Returns {"port": int, "url": str}.
    """
    port = start_web_server()
    url = f"http://127.0.0.1:{port}"

    with state.lock:
        state.markdown = plan_markdown
        state.title = plan_title

    broadcast("plan_updated")
    webbrowser.open(url)

    return {"port": port, "url": url}


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
    with state.lock:
        for item in state.feedback:
            if item.id == feedback_id:
                item.status = "processed"
                return {"ok": True}
    return {"ok": False, "error": f"Feedback item {feedback_id!r} not found"}


@mcp.tool()
def update_plan(plan_markdown: str) -> dict[str, object]:
    """Replace the entire plan with updated markdown. Browser auto-refreshes via SSE."""
    with state.lock:
        state.markdown = plan_markdown
        comment_state = _reanchor_all_comments(plan_markdown, state.feedback)
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
        broadcast("plan_updated", {"section": section_title, "comments": comment_state})
    return {"ok": found, "warning": warning}


@mcp.tool()
def reply_to_feedback(
    feedback_id: str,
    message: str,
    pushback_reasoning: str | None = None,
) -> dict[str, object]:
    """Reply to a user's feedback comment. Appears as a threaded reply in the margin.

    If pushback_reasoning is provided, the reply is styled as a disagreement
    with a distinct yellow visual treatment.
    """
    reply = Reply(
        id=str(uuid.uuid4()),
        feedback_id=feedback_id,
        author="claude",
        message=message,
        timestamp=datetime.now(UTC).isoformat(),
        is_pushback=pushback_reasoning is not None,
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
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": serialize_reply(reply)})
        return {"ok": True, "reply_id": reply.id}
    return {"ok": False, "error": f"Feedback {feedback_id!r} not found"}
