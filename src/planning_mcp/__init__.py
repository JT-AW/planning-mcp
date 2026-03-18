"""Planning MCP — interactive plan review with browser annotation."""

from __future__ import annotations

import json
import queue
import re
import socket
import threading
import time
import uuid
import webbrowser
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

# ── Data models ───────────────────────────────────────────────────────────────


@dataclass
class Reply:
    id: str
    feedback_id: str
    author: Literal["user", "claude"]
    message: str
    timestamp: str
    is_pushback: bool = False
    pushback_reasoning: str | None = None


@dataclass
class FeedbackItem:
    id: str
    type: Literal["investigate", "update_opinion", "overall"]
    selected_text: str
    anchor_context: str
    user_message: str
    timestamp: str
    status: Literal["draft", "submitted", "processed"] = "draft"
    text_offset: int = -1
    orphaned: bool = False
    replies: list[Reply] = field(default_factory=list)


@dataclass
class PlanState:
    markdown: str = ""
    title: str = "Plan Review"
    feedback: list[FeedbackItem] = field(default_factory=list)
    sse_subscribers: list[queue.SimpleQueue[str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class FeedbackRequest(BaseModel):
    type: Literal["investigate", "update_opinion", "overall"]
    selected_text: str = ""
    anchor_context: str = ""
    user_message: str
    text_offset: int = -1


class ReplyRequest(BaseModel):
    message: str
    is_pushback: bool = False
    pushback_reasoning: str | None = None


# ── Section parsing ───────────────────────────────────────────────────────────

HEADER_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def replace_section_body(
    markdown: str,
    section_title: str,
    new_body: str,
) -> tuple[str, str | None]:
    """Return (updated_markdown, warning_or_none)."""
    target_norm = normalize_title(section_title)
    matches = list(HEADER_RE.finditer(markdown))
    candidates = [i for i, m in enumerate(matches) if normalize_title(m.group(2)) == target_norm]

    if not candidates:
        return markdown, (
            f"Section {section_title!r} not found — use update_plan to replace the full document"
        )

    warning: str | None = None
    if len(candidates) > 1:
        warning = f"Multiple sections named {section_title!r}; updated the first"

    idx = candidates[0]
    m = matches[idx]
    level = len(m.group(1))
    body_start = m.end() + 1

    body_end = len(markdown)
    for j in range(idx + 1, len(matches)):
        if len(matches[j].group(1)) <= level:
            body_end = matches[j].start()
            break

    updated = markdown[:body_start] + new_body.rstrip("\n") + "\n\n" + markdown[body_end:]
    return updated, warning


# ── Global state ──────────────────────────────────────────────────────────────

state = PlanState()
_server_thread: threading.Thread | None = None
_server_port: int | None = None
_uvicorn_server: uvicorn.Server | None = None


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def broadcast(event_type: str, payload: dict[str, object] | None = None) -> None:
    msg = json.dumps({"type": event_type, **(payload or {})})
    with state.lock:
        for q in state.sse_subscribers:
            q.put(msg)


# ── Re-anchoring ─────────────────────────────────────────────────────────────

MARKDOWN_STRIP_RE = re.compile(r"[#*_`\[\]()>|~\-]")


def _markdown_to_plain(markdown: str) -> str:
    """Rough markdown-to-plaintext for anchoring purposes."""
    text = MARKDOWN_STRIP_RE.sub("", markdown)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _reanchor_comment(plain_text: str, item: FeedbackItem) -> bool:
    """Try to find item.selected_text in plain_text. Returns True if found."""
    if not item.selected_text:
        return item.type == "overall"

    target = item.selected_text

    # 1. Exact match
    idx = plain_text.find(target)
    if idx != -1:
        item.text_offset = idx
        item.orphaned = False
        return True

    # 2. Normalized whitespace match
    norm_target = _normalize_ws(target)
    norm_text = _normalize_ws(plain_text)
    idx = norm_text.find(norm_target)
    if idx != -1:
        item.text_offset = idx
        item.orphaned = False
        return True

    # 3. Anchor context match
    if item.anchor_context:
        ctx = item.anchor_context
        idx = plain_text.find(ctx)
        if idx != -1:
            item.text_offset = idx
            item.orphaned = False
            return True
        # Try normalized context
        norm_ctx = _normalize_ws(ctx)
        idx = norm_text.find(norm_ctx)
        if idx != -1:
            item.text_offset = idx
            item.orphaned = False
            return True

    item.orphaned = True
    return False


def _reanchor_all_comments(markdown: str) -> list[dict[str, object]]:
    """Re-anchor all feedback items against new markdown. Returns comment state for SSE."""
    plain = _markdown_to_plain(markdown)
    result: list[dict[str, object]] = []
    for item in state.feedback:
        _reanchor_comment(plain, item)
        result.append(
            {
                "id": item.id,
                "text_offset": item.text_offset,
                "orphaned": item.orphaned,
            }
        )
    return result


def _serialize_reply(r: Reply) -> dict[str, object]:
    return {
        "id": r.id,
        "feedback_id": r.feedback_id,
        "author": r.author,
        "message": r.message,
        "timestamp": r.timestamp,
        "is_pushback": r.is_pushback,
        "pushback_reasoning": r.pushback_reasoning,
    }


def _serialize_feedback(f: FeedbackItem) -> dict[str, object]:
    return {
        "id": f.id,
        "type": f.type,
        "selected_text": f.selected_text,
        "anchor_context": f.anchor_context,
        "user_message": f.user_message,
        "timestamp": f.timestamp,
        "status": f.status,
        "text_offset": f.text_offset,
        "orphaned": f.orphaned,
        "replies": [_serialize_reply(r) for r in f.replies],
    }


# ── FastAPI app ───────────────────────────────────────────────────────────────

api = FastAPI(title="Planning MCP UI", docs_url=None, redoc_url=None)


@api.get("/", response_class=HTMLResponse)
def get_ui() -> str:
    return HTML_TEMPLATE


@api.get("/plan")
def get_plan() -> JSONResponse:
    with state.lock:
        return JSONResponse({"markdown": state.markdown, "title": state.title})


@api.get("/feedback/all")
def get_all_feedback() -> JSONResponse:
    """Return all feedback items with replies (for page load/refresh)."""
    with state.lock:
        return JSONResponse([_serialize_feedback(f) for f in state.feedback])


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
    reply = Reply(
        id=str(uuid.uuid4()),
        feedback_id=feedback_id,
        author="user",
        message=body.message,
        timestamp=datetime.now(UTC).isoformat(),
        is_pushback=body.is_pushback,
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
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": _serialize_reply(reply)})
        return JSONResponse({"id": reply.id})
    return JSONResponse({"error": "not found"}, status_code=404)


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


# ── Uvicorn management ────────────────────────────────────────────────────────


def _start_web_server() -> int:
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


# ── FastMCP server ────────────────────────────────────────────────────────────

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
    port = _start_web_server()
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
        return [_serialize_feedback(f) for f in state.feedback if f.status == "submitted"]


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
        comment_state = _reanchor_all_comments(plan_markdown)
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
            comment_state = _reanchor_all_comments(updated)
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
        broadcast("reply_added", {"feedback_id": feedback_id, "reply": _serialize_reply(reply)})
        return {"ok": True, "reply_id": reply.id}
    return {"ok": False, "error": f"Feedback {feedback_id!r} not found"}


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """stdio entry point for Claude Code MCP integration."""
    mcp.run()


# ── HTML template ─────────────────────────────────────────────────────────────
# fmt: off
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title id="page-title">Plan Review</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/marked@9/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }

    :root {
      --bg: #fafafa;
      --surface: #ffffff;
      --border: #e4e4e7;
      --border-hover: #a1a1aa;
      --text: #09090b;
      --text-secondary: #52525b;
      --text-muted: #a1a1aa;
      --accent: #18181b;
      --accent-light: #f4f4f5;
      --accent-border: #d4d4d8;
      --amber: #d97706;
      --amber-light: #fefce8;
      --amber-border: #fde68a;
      --green: #15803d;
      --green-light: #f0fdf4;
      --purple: #7c3aed;
      --radius: 6px;
      --radius-sm: 4px;
    }

    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.6;
      color: var(--text);
      background: var(--bg);
      margin: 0;
      padding: 0 0 130px;
      -webkit-font-smoothing: antialiased;
      letter-spacing: -0.006em;
    }

    /* ── Header ─────────────────────────────────────── */
    .header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 12px 32px;
      position: sticky;
      top: 0;
      z-index: 30;
    }
    .header-inner {
      margin: 0 auto;
      max-width: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .header h1 {
      margin: 0;
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      letter-spacing: -0.02em;
    }
    .header-badge {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-secondary);
      background: var(--accent-light);
      border: 1px solid var(--border);
      padding: 2px 8px;
      border-radius: var(--radius-sm);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    /* ── Layout ──────────────────────────────────────── */
    .layout {
      display: flex;
      margin: 20px auto 0;
      padding: 0 32px;
      gap: 24px;
      position: relative;
    }
    .main-content {
      flex: 1;
      min-width: 0;
    }
    .comment-margin {
      width: 280px;
      flex-shrink: 0;
      position: sticky;
      top: 52px;
      max-height: calc(100vh - 52px - 130px);
      overflow-y: auto;
      padding-bottom: 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    @media (max-width: 900px) {
      .layout { flex-direction: column; }
      .comment-margin { width: 100%; position: static; max-height: none; }
    }

    /* ── Section blocks ─────────────────────────────── */
    .section-block {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px 24px;
      margin-bottom: 8px;
      transition: border-color 0.12s;
    }
    .section-block:hover {
      border-color: var(--border-hover);
    }

    /* ── Markdown content ───────────────────────────── */
    .section-content h1 { font-size: 20px; font-weight: 600; margin: 0 0 10px; letter-spacing: -0.025em; }
    .section-content h2 { font-size: 16px; font-weight: 600; margin: 0 0 8px; letter-spacing: -0.02em; color: var(--text); }
    .section-content h3 { font-size: 14px; font-weight: 600; margin: 0 0 6px; color: var(--text); }
    .section-content h4 { font-size: 12px; font-weight: 600; margin: 0 0 6px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.06em; }
    .section-content p { margin: 0 0 10px; }
    .section-content p:last-child { margin-bottom: 0; }
    .section-content ul, .section-content ol { margin: 0 0 10px; padding-left: 20px; }
    .section-content li { margin-bottom: 2px; }
    .section-content pre {
      background: var(--accent-light);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 12px 14px;
      overflow-x: auto;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12.5px;
      line-height: 1.5;
    }
    .section-content code {
      font-family: 'JetBrains Mono', monospace;
      background: var(--accent-light);
      border-radius: 3px;
      padding: 1px 5px;
      font-size: 12.5px;
    }
    .section-content pre code { background: none; padding: 0; }
    .section-content table { border-collapse: collapse; width: 100%; font-size: 13px; margin: 0 0 10px; }
    .section-content th, .section-content td {
      border: 1px solid var(--border);
      padding: 6px 12px;
      text-align: left;
    }
    .section-content th { background: var(--accent-light); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
    .section-content blockquote {
      border-left: 2px solid var(--accent-border);
      margin: 0 0 10px;
      padding: 2px 14px;
      color: var(--text-secondary);
    }
    .section-content hr {
      border: none;
      border-top: 1px solid var(--border);
      margin: 16px 0;
    }
    .section-content strong { font-weight: 600; }

    /* ── Text highlight for annotations ─────────────── */
    mark[data-comment] {
      background: rgba(24, 24, 27, 0.06);
      border-bottom: 1.5px solid rgba(24, 24, 27, 0.25);
      border-radius: 1px;
      cursor: pointer;
      transition: background 0.1s;
    }
    mark[data-comment]:hover {
      background: rgba(24, 24, 27, 0.12);
    }
    mark[data-comment].active {
      background: rgba(250, 204, 21, 0.35);
      border-bottom: 2px solid #eab308;
      transition: background 0.15s;
    }

    @keyframes highlight-pulse {
      0% { background: rgba(250, 204, 21, 0.5); }
      100% { background: rgba(250, 204, 21, 0.15); }
    }
    mark[data-comment].pulsing {
      animation: highlight-pulse 0.8s ease-out 2;
      border-bottom: 2px solid #eab308;
    }

    /* ── Selection toolbar ──────────────────────────── */
    .selection-toolbar {
      position: fixed;
      z-index: 100;
      background: var(--accent);
      border-radius: var(--radius);
      padding: 2px;
      display: flex;
      gap: 1px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.15), 0 0 0 1px rgba(0,0,0,0.08);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.08s;
      transform: translateX(-50%);
    }
    .selection-toolbar.visible {
      opacity: 1;
      pointer-events: auto;
    }
    .selection-toolbar button {
      background: transparent;
      color: #fafafa;
      font-family: 'Inter', sans-serif;
      font-size: 12px;
      font-weight: 500;
      padding: 6px 12px;
      border: none;
      border-radius: var(--radius-sm);
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.08s;
    }
    .selection-toolbar button:hover { background: rgba(255,255,255,0.1); }
    .selection-toolbar .tb-investigate { color: #fbbf24; }
    .selection-toolbar .tb-opinion { color: #d4d4d8; }

    /* ── Comment cards ──────────────────────────────── */
    .comment-card {
      width: 100%;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px 14px;
      font-size: 13px;
      transition: border-color 0.1s;
      cursor: default;
      flex-shrink: 0;
    }
    .comment-card:hover {
      border-color: var(--border-hover);
    }
    .comment-card.draft {
      border-style: dashed;
      border-color: var(--accent-border);
    }
    .comment-card.editing {
      border-color: var(--accent);
    }

    .comment-header {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 6px;
    }
    .comment-type-badge {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 1px 6px;
      border-radius: 3px;
      border: 1px solid;
    }
    .comment-type-badge.investigate { background: var(--amber-light); color: var(--amber); border-color: var(--amber-border); }
    .comment-type-badge.update_opinion { background: var(--accent-light); color: var(--text-secondary); border-color: var(--accent-border); }
    .comment-type-badge.overall { background: var(--accent-light); color: var(--text-muted); border-color: var(--border); }

    .go-to-text {
      margin-left: auto;
      font-size: 12px;
      color: var(--text-muted);
      cursor: pointer;
      opacity: 0;
      transition: opacity 0.1s;
      padding: 0 2px;
    }
    .comment-card:hover .go-to-text { opacity: 1; }
    .go-to-text:hover { color: var(--text); }

    .comment-quote {
      font-size: 12px;
      color: var(--text-muted);
      border-left: 2px solid var(--accent-border);
      padding: 1px 0 1px 8px;
      margin-bottom: 6px;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      line-height: 1.45;
    }

    .comment-message {
      color: var(--text);
      line-height: 1.5;
      margin-bottom: 4px;
    }

    .comment-time {
      font-size: 11px;
      color: var(--text-muted);
    }

    /* ── Comment editing ────────────────────────────── */
    .comment-edit-area textarea {
      width: 100%;
      min-height: 56px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 8px 10px;
      font-family: 'Inter', sans-serif;
      font-size: 13px;
      resize: none;
      outline: none;
      line-height: 1.5;
      overflow: hidden;
    }
    .comment-edit-area textarea:focus {
      border-color: var(--accent);
    }
    .comment-edit-actions {
      margin-top: 6px;
      display: flex;
      justify-content: flex-end;
      gap: 4px;
    }

    /* ── Thread replies ─────────────────────────────── */
    .thread { margin-top: 8px; }
    .thread-reply {
      padding: 6px 0 0;
      border-top: 1px solid var(--border);
      margin-top: 6px;
    }
    .thread-reply:first-child { margin-top: 0; }
    .reply-author {
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 2px;
    }
    .reply-author.user { color: var(--text-secondary); }
    .reply-author.claude { color: var(--purple); }
    .reply-message { font-size: 13px; color: var(--text); line-height: 1.45; }

    .thread-reply.pushback {
      background: var(--amber-light);
      border: 1px solid var(--amber-border);
      border-radius: var(--radius-sm);
      padding: 6px 8px;
      margin-top: 6px;
    }
    .pushback-label {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--amber);
      margin-bottom: 2px;
    }

    .reply-trigger {
      font-size: 12px;
      color: var(--text-muted);
      cursor: pointer;
      margin-top: 6px;
      display: inline-block;
    }
    .reply-trigger:hover { color: var(--text); }

    .reply-input-area { margin-top: 6px; }
    .reply-input-area textarea {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 6px 8px;
      font-family: 'Inter', sans-serif;
      font-size: 12px;
      resize: none;
      outline: none;
      overflow: hidden;
    }
    .reply-input-area textarea:focus { border-color: var(--accent); }
    .reply-input-actions {
      margin-top: 4px;
      display: flex;
      justify-content: flex-end;
      gap: 4px;
    }

    /* ── Orphaned comments ──────────────────────────── */
    .comment-card.orphaned {
      opacity: 0.6;
      border-style: dotted;
    }
    .orphan-badge {
      font-size: 10px;
      color: var(--amber);
      background: var(--amber-light);
      border: 1px solid var(--amber-border);
      border-radius: 3px;
      padding: 0 5px;
      font-weight: 600;
    }

    /* ── Buttons ─────────────────────────────────────── */
    button {
      font-family: 'Inter', sans-serif;
      font-size: 12px;
      font-weight: 500;
      border: none;
      border-radius: var(--radius-sm);
      padding: 5px 10px;
      cursor: pointer;
      line-height: 1.4;
      white-space: nowrap;
      transition: background 0.08s;
    }
    .btn-primary { background: var(--accent); color: white; }
    .btn-primary:hover { background: #27272a; }
    .btn-ghost { background: transparent; color: var(--text-secondary); border: 1px solid var(--border); }
    .btn-ghost:hover { background: var(--accent-light); }
    .btn-sm { font-size: 11px; padding: 3px 7px; }

    /* ── Footer ──────────────────────────────────────── */
    .footer {
      position: fixed;
      bottom: 0;
      left: 0;
      right: 0;
      background: var(--surface);
      border-top: 1px solid var(--border);
      padding: 12px 32px;
      z-index: 30;
    }
    .footer-inner {
      margin: 0 auto;
      max-width: none;
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
    .footer-textarea-wrap { flex: 1; }
    .footer-label {
      font-size: 11px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 4px;
      display: flex;
      align-items: center;
      gap: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .pending-count {
      background: var(--accent);
      color: white;
      font-size: 10px;
      font-weight: 700;
      border-radius: 3px;
      padding: 0 6px;
      display: none;
    }
    .pending-count.visible { display: inline-block; }
    .footer-textarea {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 8px 10px;
      font-family: 'Inter', sans-serif;
      font-size: 13px;
      resize: none;
      height: 44px;
      outline: none;
      transition: border-color 0.08s;
    }
    .footer-textarea:focus {
      border-color: var(--accent);
    }
    .footer-submit-wrap { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; }
    .btn-revise {
      background: var(--accent);
      color: white;
      font-size: 13px;
      font-weight: 600;
      padding: 8px 16px;
      border-radius: var(--radius);
      white-space: nowrap;
    }
    .btn-revise:hover { background: #27272a; }
    .btn-revise:disabled { background: var(--accent-border); cursor: default; }
    .footer-status { font-size: 11px; color: var(--text-muted); text-align: right; }
    .footer-status.sent { color: var(--green); font-weight: 500; }
  </style>
</head>
<body>
  <div class="header">
    <div class="header-inner">
      <h1 id="plan-title">Loading...</h1>
      <span class="header-badge">Plan Review</span>
    </div>
  </div>

  <div class="layout">
    <div class="main-content" id="plan-content"></div>
    <div class="comment-margin" id="comment-margin"></div>
  </div>

  <!-- Selection toolbar (hidden until text selected) -->
  <div class="selection-toolbar" id="selection-toolbar">
    <button class="tb-investigate" data-tb-action="investigate">Investigate</button>
    <button class="tb-opinion" data-tb-action="update_opinion">Update &middot; Opinion</button>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div class="footer-inner">
      <div class="footer-textarea-wrap">
        <div class="footer-label">
          Overall feedback
          <span class="pending-count" id="pending-count"></span>
        </div>
        <textarea class="footer-textarea" id="overall-feedback"
          placeholder="Structural feedback for the whole plan..."></textarea>
      </div>
      <div class="footer-submit-wrap">
        <button class="btn-revise" id="submit-btn">Submit &amp; Revise</button>
        <div class="footer-status" id="footer-status"></div>
      </div>
    </div>
  </div>

  <script>
  // ═══════════════════════════════════════════════════════════════════════════
  // State
  // ═══════════════════════════════════════════════════════════════════════════
  let planData = { markdown: "", title: "" };
  let comments = [];       // all comments (draft + submitted + processed)
  let nextLocalId = 1;

  // ═══════════════════════════════════════════════════════════════════════════
  // SSE
  // ═══════════════════════════════════════════════════════════════════════════
  function connectSSE() {
    const es = new EventSource("/events");
    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "plan_updated") {
        // Apply server-side anchor hints before re-rendering
        if (msg.comments) applyServerAnchors(msg.comments);
        fetchAndRender();
      } else if (msg.type === "reply_added") {
        handleReplyAdded(msg);
      }
    };
    es.onerror = () => { es.close(); setTimeout(connectSSE, 2000); };
  }

  function handleReplyAdded(msg) {
    const c = comments.find(c => c.serverId === msg.feedback_id);
    if (!c) return;
    if (!c.replies) c.replies = [];
    c.replies.push(msg.reply);
    renderCommentCards();
  }

  function applyServerAnchors(serverComments) {
    serverComments.forEach(sc => {
      const c = comments.find(c => c.serverId === sc.id);
      if (c) {
        c.textOffset = sc.text_offset;
        c.orphaned = sc.orphaned;
      }
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Fetch + render
  // ═══════════════════════════════════════════════════════════════════════════
  async function fetchAndRender() {
    const res = await fetch("/plan");
    planData = await res.json();
    document.getElementById("page-title").textContent = planData.title;
    document.getElementById("plan-title").textContent = planData.title;
    renderPlan(planData.markdown);
    reanchorComments();
    renderCommentCards();
  }

  async function loadExistingComments() {
    try {
      const res = await fetch("/feedback/all");
      const items = await res.json();
      items.forEach(item => {
        if (!comments.find(c => c.serverId === item.id)) {
          comments.push({
            localId: "server-" + (nextLocalId++),
            serverId: item.id,
            type: item.type,
            selectedText: item.selected_text,
            anchorContext: item.anchor_context,
            userMessage: item.user_message,
            timestamp: item.timestamp,
            status: item.status,  // draft | submitted | processed
            textOffset: item.text_offset,
            replies: item.replies || [],
            orphaned: item.orphaned || false,
          });
        }
      });
    } catch { /* ignore on first load */ }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Section parsing + rendering
  // ═══════════════════════════════════════════════════════════════════════════
  function parseSections(markdown) {
    const headerRe = /^(#{1,4})\s+(.+)$/gm;
    const matches = [...markdown.matchAll(headerRe)];
    if (matches.length === 0) {
      return [{ level: 0, title: "__root__", headerLine: "", body: markdown }];
    }
    return matches.map((m, i) => {
      const level = m[1].length;
      const title = m[2].trim();
      const headerLine = m[0];
      const bodyStart = m.index + m[0].length + 1;
      let bodyEnd = markdown.length;
      for (let j = i + 1; j < matches.length; j++) {
        if (matches[j][1].length <= level) { bodyEnd = matches[j].index; break; }
      }
      return { level, title, headerLine, body: markdown.slice(bodyStart, bodyEnd) };
    });
  }

  function renderPlan(markdown) {
    const container = document.getElementById("plan-content");
    container.textContent = "";

    parseSections(markdown).forEach(sec => {
      const block = document.createElement("div");
      block.className = "section-block";

      const contentDiv = document.createElement("div");
      contentDiv.className = "section-content";
      const raw = marked.parse(sec.headerLine + (sec.headerLine ? "\n" : "") + sec.body);
      contentDiv.innerHTML = DOMPurify.sanitize(raw);
      block.appendChild(contentDiv);

      container.appendChild(block);
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Text selection toolbar
  // ═══════════════════════════════════════════════════════════════════════════
  const toolbar = document.getElementById("selection-toolbar");
  let savedRange = null;

  document.querySelector(".main-content").addEventListener("mouseup", (e) => {
    // Small delay to let selection settle
    setTimeout(() => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.toString().trim() === "") {
        return; // don't hide here — let mousedown handle it
      }
      // Only show if selection is inside main-content
      const mainEl = document.querySelector(".main-content");
      if (!mainEl.contains(sel.anchorNode) || !mainEl.contains(sel.focusNode)) return;

      savedRange = sel.getRangeAt(0).cloneRange();
      const rect = sel.getRangeAt(0).getBoundingClientRect();
      // Fixed positioning — use viewport coords directly, no scrollY
      const toolbarW = toolbar.offsetWidth || 200;
      let left = rect.left + rect.width / 2;
      // Clamp to viewport edges
      left = Math.max(toolbarW / 2 + 8, Math.min(left, window.innerWidth - toolbarW / 2 - 8));
      toolbar.style.left = `${left}px`;
      toolbar.style.top = `${Math.max(8, rect.top - 44)}px`;
      toolbar.classList.add("visible");
    }, 10);
  });

  document.addEventListener("mousedown", (e) => {
    if (toolbar.contains(e.target)) return;
    toolbar.classList.remove("visible");
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toolbar.classList.remove("visible");
  });

  // Toolbar button clicks
  toolbar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-tb-action]");
    if (!btn || !savedRange) return;

    const action = btn.dataset.tbAction;
    const selectedText = savedRange.toString().trim();
    const anchorContext = getAnchorContext(savedRange);
    const textOffset = getTextOffset(savedRange);
    const commentId = "local-" + (nextLocalId++);

    // Highlight the selected text
    highlightRange(savedRange, commentId);

    // Create a comment in editing mode
    comments.push({
      localId: commentId,
      serverId: null,
      type: action,
      selectedText: selectedText,
      anchorContext: anchorContext,
      textOffset: textOffset,
      userMessage: "",
      timestamp: null,
      status: "editing",
      replies: [],
      orphaned: false,
    });

    toolbar.classList.remove("visible");
    window.getSelection().removeAllRanges();
    renderCommentCards();

    // Focus the textarea in the new card
    setTimeout(() => {
      const card = document.querySelector(`.comment-card[data-local-id="${commentId}"]`);
      if (card) {
        const ta = card.querySelector("textarea");
        if (ta) ta.focus();
      }
    }, 50);
  });

  function getAnchorContext(range) {
    const container = range.startContainer.parentElement
      ? range.startContainer.parentElement.closest(".section-content")
      : null;
    if (!container) return range.toString();
    const fullText = container.textContent;
    const selectedText = range.toString();
    const idx = fullText.indexOf(selectedText);
    if (idx === -1) return selectedText;
    const start = Math.max(0, idx - 50);
    const end = Math.min(fullText.length, idx + selectedText.length + 50);
    return fullText.slice(start, end);
  }

  function getTextOffset(range) {
    // Compute character offset of selection start within .main-content textContent
    const mainEl = document.querySelector(".main-content");
    const fullText = mainEl.textContent;
    const selectedText = range.toString();
    const idx = fullText.indexOf(selectedText);
    return idx !== -1 ? idx : -1;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Highlight wrapping
  // ═══════════════════════════════════════════════════════════════════════════
  function highlightRange(range, commentId) {
    // Handle cross-element selections by walking text nodes
    const textNodes = getTextNodesInRange(range);
    textNodes.forEach(({ node, startOffset, endOffset }) => {
      const text = node.textContent;
      const before = text.slice(0, startOffset);
      const middle = text.slice(startOffset, endOffset);
      const after = text.slice(endOffset);

      const parent = node.parentNode;
      const frag = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));

      const mark = document.createElement("mark");
      mark.dataset.comment = commentId;
      mark.textContent = middle;
      frag.appendChild(mark);

      if (after) frag.appendChild(document.createTextNode(after));
      parent.replaceChild(frag, node);
    });
  }

  function getTextNodesInRange(range) {
    const result = [];
    const walker = document.createTreeWalker(
      range.commonAncestorContainer.nodeType === 1
        ? range.commonAncestorContainer
        : range.commonAncestorContainer.parentElement,
      NodeFilter.SHOW_TEXT
    );

    let node;
    let inRange = false;
    while ((node = walker.nextNode())) {
      if (node === range.startContainer) {
        inRange = true;
        const start = range.startOffset;
        const end = node === range.endContainer ? range.endOffset : node.textContent.length;
        if (start < end) result.push({ node, startOffset: start, endOffset: end });
        if (node === range.endContainer) break;
        continue;
      }
      if (node === range.endContainer) {
        if (range.endOffset > 0) {
          result.push({ node, startOffset: 0, endOffset: range.endOffset });
        }
        break;
      }
      if (inRange) {
        result.push({ node, startOffset: 0, endOffset: node.textContent.length });
      }
    }
    return result;
  }

  function highlightTextInDOMWithHint(text, commentId, hintOffset) {
    // Re-highlight text after re-render. Uses hintOffset to narrow search.
    const mainEl = document.querySelector(".main-content");
    const fullText = mainEl.textContent;

    // Find the text, preferring near hintOffset
    let targetIdx = -1;
    if (hintOffset >= 0) {
      // Search in a window around the hint first
      const windowStart = Math.max(0, hintOffset - 50);
      const windowEnd = Math.min(fullText.length, hintOffset + text.length + 50);
      const window = fullText.slice(windowStart, windowEnd);
      const localIdx = window.indexOf(text);
      if (localIdx !== -1) targetIdx = windowStart + localIdx;
    }
    // Fallback to full search
    if (targetIdx === -1) targetIdx = fullText.indexOf(text);
    if (targetIdx === -1) return false;

    // Walk text nodes to find the one containing targetIdx
    const walker = document.createTreeWalker(mainEl, NodeFilter.SHOW_TEXT);
    let node;
    let charCount = 0;
    while ((node = walker.nextNode())) {
      const nodeLen = node.textContent.length;
      if (charCount + nodeLen <= targetIdx) {
        charCount += nodeLen;
        continue;
      }
      // Skip if already inside a mark
      if (node.parentNode.tagName === "MARK" && node.parentNode.dataset.comment) {
        charCount += nodeLen;
        continue;
      }

      const localStart = targetIdx - charCount;
      const localEnd = Math.min(localStart + text.length, nodeLen);
      const before = node.textContent.slice(0, localStart);
      const middle = node.textContent.slice(localStart, localEnd);
      const after = node.textContent.slice(localEnd);

      const parent = node.parentNode;
      const frag = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));
      const mark = document.createElement("mark");
      mark.dataset.comment = commentId;
      mark.textContent = middle;
      frag.appendChild(mark);
      if (after) frag.appendChild(document.createTextNode(after));
      parent.replaceChild(frag, node);
      return true;
    }
    return false;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Re-anchoring
  // ═══════════════════════════════════════════════════════════════════════════
  function reanchorComments() {
    comments.forEach(c => {
      if (c.type === "overall" || c.status === "editing") return;
      const markId = getMarkId(c);  // always use localId for DOM marks

      // Use server-provided textOffset as hint for narrowed search
      const found = highlightTextInDOMWithHint(c.selectedText, markId, c.textOffset);
      if (!found) {
        // Fallback: try anchor context
        const ctxFound = c.anchorContext
          ? highlightTextInDOMWithHint(c.anchorContext, markId, c.textOffset)
          : false;
        if (!ctxFound) c.orphaned = true;
      } else {
        c.orphaned = false;
      }
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Comment card rendering
  // ═══════════════════════════════════════════════════════════════════════════
  function renderCommentCards() {
    const margin = document.getElementById("comment-margin");
    margin.textContent = "";

    // Sort by anchor position (nulls — no highlight found — go last)
    const sorted = [...comments].sort((a, b) => {
      const aY = getHighlightTop(getMarkId(a));
      const bY = getHighlightTop(getMarkId(b));
      if (aY === null && bY === null) return 0;
      if (aY === null) return 1;
      if (bY === null) return -1;
      return aY - bY;
    });

    sorted.forEach(c => {
      const card = buildCommentCard(c);
      margin.appendChild(card);
    });

    updatePendingCount();
  }

  function getMarkId(c) {
    // Marks are always created with localId; use that for DOM lookups
    return c.localId;
  }

  function getHighlightTop(markId) {
    const mark = document.querySelector(`mark[data-comment="${CSS.escape(String(markId))}"]`);
    if (!mark) return null;
    return mark.getBoundingClientRect().top + window.scrollY;
  }

  function buildCommentCard(c) {
    const card = document.createElement("div");
    const markId = getMarkId(c);
    card.className = `comment-card ${c.status}${c.orphaned ? " orphaned" : ""}`;
    card.dataset.localId = c.localId;

    // Header
    const header = document.createElement("div");
    header.className = "comment-header";

    const badge = document.createElement("span");
    badge.className = `comment-type-badge ${c.type}`;
    badge.textContent = c.type === "investigate" ? "Investigate"
      : c.type === "update_opinion" ? "Update" : "Overall";
    header.appendChild(badge);

    if (c.orphaned) {
      const ob = document.createElement("span");
      ob.className = "orphan-badge";
      ob.textContent = "text changed";
      header.appendChild(ob);
    }

    // "Go to text" link — scroll to the highlight
    if (c.type !== "overall" && c.status !== "editing") {
      const goLink = document.createElement("span");
      goLink.className = "go-to-text";
      goLink.textContent = "\u2197";  // ↗ arrow
      goLink.title = "Scroll to highlighted text";
      goLink.addEventListener("click", (e) => {
        e.stopPropagation();
        const marks = document.querySelectorAll(`mark[data-comment="${CSS.escape(String(markId))}"]`);
        if (marks.length > 0) {
          marks[0].scrollIntoView({ behavior: "smooth", block: "center" });
          marks.forEach(m => {
            m.classList.remove("pulsing");
            void m.offsetWidth;  // force reflow to restart animation
            m.classList.add("pulsing");
          });
          setTimeout(() => marks.forEach(m => m.classList.remove("pulsing")), 2000);
        }
      });
      header.appendChild(goLink);
    }

    card.appendChild(header);

    // Quoted text
    if (c.selectedText && c.type !== "overall") {
      const quote = document.createElement("div");
      quote.className = "comment-quote";
      quote.textContent = c.selectedText;
      card.appendChild(quote);
    }

    if (c.status === "editing") {
      // Editing mode
      const editArea = document.createElement("div");
      editArea.className = "comment-edit-area";

      const ta = document.createElement("textarea");
      ta.placeholder = c.type === "investigate"
        ? "What should Claude verify?"
        : "What do you want changed?";
      // Auto-grow textarea
      ta.addEventListener("input", () => {
        ta.style.height = "auto";
        ta.style.height = ta.scrollHeight + "px";
      });
      // Cmd+Enter to submit
      ta.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
          e.preventDefault();
          addBtn.click();
        }
      });
      editArea.appendChild(ta);

      const actions = document.createElement("div");
      actions.className = "comment-edit-actions";

      const cancelBtn = document.createElement("button");
      cancelBtn.className = "btn-ghost btn-sm";
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", () => {
        // Remove the comment and its highlight
        removeHighlight(markId);
        comments = comments.filter(x => x.localId !== c.localId);
        renderCommentCards();
      });

      const addBtn = document.createElement("button");
      addBtn.className = "btn-primary btn-sm";
      addBtn.textContent = "Add Comment";
      addBtn.addEventListener("click", async () => {
        const text = ta.value.trim();
        if (!text) { ta.focus(); return; }
        c.userMessage = text;
        c.status = "draft";
        c.timestamp = new Date().toISOString();
        // Auto-POST to server as draft for persistence
        try {
          const res = await fetch("/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              type: c.type,
              selected_text: c.selectedText,
              anchor_context: c.anchorContext,
              user_message: c.userMessage,
              text_offset: c.textOffset || -1,
            }),
          });
          const data = await res.json();
          c.serverId = data.id;
        } catch { /* will retry on submit */ }
        renderCommentCards();
      });

      actions.appendChild(cancelBtn);
      actions.appendChild(addBtn);
      editArea.appendChild(actions);
      card.appendChild(editArea);
    } else {
      // Display mode
      const msg = document.createElement("div");
      msg.className = "comment-message";
      msg.textContent = c.userMessage;
      card.appendChild(msg);

      // Replies
      if (c.replies && c.replies.length > 0) {
        const thread = document.createElement("div");
        thread.className = "thread";
        c.replies.forEach(r => {
          const reply = document.createElement("div");
          reply.className = `thread-reply${r.is_pushback ? " pushback" : ""}`;

          if (r.is_pushback && r.pushback_reasoning) {
            const pbLabel = document.createElement("div");
            pbLabel.className = "pushback-label";
            pbLabel.textContent = "Claude disagrees";
            reply.appendChild(pbLabel);
          }

          const author = document.createElement("div");
          author.className = `reply-author ${r.author}`;
          author.textContent = r.author === "claude" ? "Claude" : "You";
          reply.appendChild(author);

          const rmsg = document.createElement("div");
          rmsg.className = "reply-message";
          rmsg.textContent = r.is_pushback ? (r.pushback_reasoning || r.message) : r.message;
          reply.appendChild(rmsg);

          thread.appendChild(reply);
        });
        card.appendChild(thread);
      }

      // Reply trigger
      if (c.status === "submitted" && c.serverId) {
        const trigger = document.createElement("span");
        trigger.className = "reply-trigger";
        trigger.textContent = "Reply";
        trigger.addEventListener("click", () => showReplyInput(card, c));
        card.appendChild(trigger);
      }

      // Timestamp
      if (c.timestamp) {
        const ts = document.createElement("div");
        ts.className = "comment-time";
        const d = new Date(c.timestamp);
        ts.textContent = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        card.appendChild(ts);
      }
    }

    // No catch-all card click — scroll is handled by the "go to text" link in header

    return card;
  }

  function removeHighlight(commentId) {
    document.querySelectorAll(`mark[data-comment="${CSS.escape(String(commentId))}"]`).forEach(mark => {
      const parent = mark.parentNode;
      while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
      parent.removeChild(mark);
      parent.normalize();
    });
  }

  function showReplyInput(card, comment) {
    if (card.querySelector(".reply-input-area")) return;
    const area = document.createElement("div");
    area.className = "reply-input-area";
    const ta = document.createElement("textarea");
    ta.placeholder = "Reply...";
    ta.addEventListener("input", () => {
      ta.style.height = "auto";
      ta.style.height = ta.scrollHeight + "px";
    });
    ta.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        sendBtn.click();
      }
    });
    area.appendChild(ta);

    const actions = document.createElement("div");
    actions.className = "reply-input-actions";
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn-ghost btn-sm";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => area.remove());
    const sendBtn = document.createElement("button");
    sendBtn.className = "btn-primary btn-sm";
    sendBtn.textContent = "Reply";
    sendBtn.addEventListener("click", async () => {
      const text = ta.value.trim();
      if (!text) return;
      await fetch(`/feedback/${comment.serverId}/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      // SSE will handle rendering the reply
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(sendBtn);
    area.appendChild(actions);
    card.appendChild(area);
    ta.focus();
  }

  // Click highlight → scroll to card
  document.querySelector(".main-content").addEventListener("click", (e) => {
    const mark = e.target.closest("mark[data-comment]");
    if (!mark) return;
    const commentId = mark.dataset.comment;
    const card = document.querySelector(`.comment-card[data-local-id="${CSS.escape(commentId)}"]`)
      || document.querySelector(`.comment-card`); // fallback
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  });

  // Reposition on resize
  window.addEventListener("resize", () => { renderCommentCards(); });

  // ═══════════════════════════════════════════════════════════════════════════
  // Global submit
  // ═══════════════════════════════════════════════════════════════════════════
  async function submitAllFeedback() {
    const btn = document.getElementById("submit-btn");
    const statusEl = document.getElementById("footer-status");
    const overallText = document.getElementById("overall-feedback").value.trim();

    const drafts = comments.filter(c => c.status === "draft");

    // POST overall as a draft first if present
    if (overallText) {
      try {
        const res = await fetch("/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: "overall", selected_text: "", anchor_context: "", user_message: overallText, text_offset: -1 }),
        });
        const data = await res.json();
        comments.push({
          localId: "local-" + (nextLocalId++),
          serverId: data.id,
          type: "overall",
          selectedText: "",
          anchorContext: "",
          textOffset: -1,
          userMessage: overallText,
          timestamp: new Date().toISOString(),
          status: "draft",
          replies: [],
          orphaned: false,
        });
      } catch { /* proceed anyway */ }
    }

    if (drafts.length === 0 && !overallText) {
      statusEl.textContent = "No feedback to submit.";
      statusEl.className = "footer-status";
      return;
    }

    btn.disabled = true;
    statusEl.textContent = "Sending...";
    statusEl.className = "footer-status";

    try {
      await fetch("/feedback/submit-all", { method: "POST" });

      // Transition all drafts to submitted locally
      comments.forEach(c => {
        if (c.status === "draft") c.status = "submitted";
      });

      document.getElementById("overall-feedback").value = "";
      renderCommentCards();
      statusEl.textContent = "Sent \u2014 Claude is revising...";
      statusEl.className = "footer-status sent";
    } catch {
      statusEl.textContent = "Error sending. Try again.";
      statusEl.className = "footer-status";
    } finally {
      btn.disabled = false;
    }
  }

  document.getElementById("submit-btn").addEventListener("click", submitAllFeedback);

  // Cmd+Enter on overall feedback textarea triggers submit
  document.getElementById("overall-feedback").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submitAllFeedback();
    }
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // Helpers
  // ═══════════════════════════════════════════════════════════════════════════
  function updatePendingCount() {
    const badge = document.getElementById("pending-count");
    const n = comments.filter(c => c.status === "draft").length;
    if (n === 0) {
      badge.classList.remove("visible");
    } else {
      badge.textContent = `${n} draft${n === 1 ? "" : "s"}`;
      badge.classList.add("visible");
    }
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Init
  // ═══════════════════════════════════════════════════════════════════════════
  (async () => {
    await fetchAndRender();
    await loadExistingComments();
    reanchorComments();
    renderCommentCards();
    connectSSE();
  })();
  </script>
</body>
</html>"""
# fmt: on
