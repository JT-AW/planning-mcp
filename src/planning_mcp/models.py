"""Data models for the planning MCP server."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel


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
