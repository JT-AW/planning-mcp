"""FastMCP server and MCP tool definitions."""

from __future__ import annotations

import threading
import uuid
import webbrowser
from datetime import UTC, date, datetime
from pathlib import Path

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
        "to respond to specific comments in-thread. Use accept_plan to save "
        "the plan to a file when the user is satisfied.\n\n"
        "IMPORTANT: When calling open_plan or update_plan, prefer writing the "
        "markdown to a temporary file and passing plan_file instead of inlining "
        "the full content in plan_markdown. This avoids bloating the tool call "
        "payload. Example: write to /tmp/plan.md, then call "
        'open_plan(plan_file="/tmp/plan.md", plan_title="My Plan").'
    ),
)


# ── Plan tools ───────────────────────────────────────────────────────────────


@mcp.tool()
def open_plan(
    plan_markdown: str = "",
    plan_file: str = "",
    plan_title: str = "Plan Review",
) -> dict[str, object]:
    """Publish a plan to the browser for interactive review.

    PREFERRED: Write markdown to a temp file and pass plan_file="/tmp/plan.md"
    instead of inlining content in plan_markdown. This keeps the tool call small.

    If both are given, plan_file takes precedence.
    Starts the web server on first call (subsequent calls reuse it).
    Opens the browser automatically. Returns {"port": int, "url": str}.
    """
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

    broadcast("plan_updated")
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()

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
    pushback_type: str = "none",
    pushback_reasoning: str | None = None,
) -> dict[str, object]:
    """Reply to a user's feedback comment. Appears as a threaded reply in the margin.

    pushback_type can be "none", "disagree", or "alternative".
    If "disagree" or "alternative", provide pushback_reasoning for context.
    The reply gets distinct visual treatment in the browser.
    """
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
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": serialize_reply(reply)})
        return {"ok": True, "reply_id": reply.id}
    return {"ok": False, "error": f"Feedback {feedback_id!r} not found"}


@mcp.tool()
def accept_plan(save_path: str) -> dict[str, object]:
    """Accept the current plan and save it to a file.

    Writes the plan markdown with YAML frontmatter to the specified path.
    Appends a Review Comments section with all feedback and replies.
    """
    path = Path(save_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with state.lock:
        markdown = state.markdown
        title = state.title
        feedback_items = [f for f in state.feedback if f.status in ("submitted", "processed")]

    # Build review comments section
    comments_section = ""
    if feedback_items:
        lines = ["\n\n---\n\n## Review Comments\n"]
        for item in feedback_items:
            lines.append(f"\n### {item.type.replace('_', ' ').title()}")
            if item.selected_text:
                lines.append(f"> {item.selected_text[:200]}")
            lines.append(f"\n{item.user_message}")
            for reply in item.replies:
                author = "Claude" if reply.author == "claude" else "User"
                prefix = ""
                if reply.pushback_type != "none":
                    prefix = f" [{reply.pushback_type}]"
                lines.append(f"\n**{author}{prefix}:** {reply.message}")
        comments_section = "\n".join(lines)

    content = (
        f'---\ntitle: "{title}"\ntags: [plan]\nstatus: accepted\n'
        f"created: {date.today()}\n---\n\n{markdown}{comments_section}\n"
    )
    path.write_text(content, encoding="utf-8")
    broadcast("plan_accepted")

    return {"ok": True, "path": str(path)}
