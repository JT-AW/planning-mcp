"""Re-anchoring logic and feedback serializers."""

from __future__ import annotations

import re

from planning_mcp.models import FeedbackItem, Reply

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


def _reanchor_all_comments(markdown: str, feedback: list[FeedbackItem]) -> list[dict[str, object]]:
    """Re-anchor all feedback items against new markdown. Returns comment state for SSE."""
    plain = _markdown_to_plain(markdown)
    result: list[dict[str, object]] = []
    for item in feedback:
        _reanchor_comment(plain, item)
        result.append(
            {
                "id": item.id,
                "text_offset": item.text_offset,
                "orphaned": item.orphaned,
            }
        )
    return result


def serialize_reply(r: Reply) -> dict[str, object]:
    return {
        "id": r.id,
        "feedback_id": r.feedback_id,
        "author": r.author,
        "message": r.message,
        "timestamp": r.timestamp,
        "pushback_type": r.pushback_type,
        "is_pushback": r.is_pushback,  # backward compat
        "pushback_reasoning": r.pushback_reasoning,
    }


def serialize_feedback(f: FeedbackItem) -> dict[str, object]:
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
        "replies": [serialize_reply(r) for r in f.replies],
    }
