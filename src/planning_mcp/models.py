"""Data models for the planning MCP server."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AccessMode = Literal["local", "ssh", "external"]


@dataclass
class Reply:
    id: str
    feedback_id: str
    author: Literal["user", "claude"]
    message: str
    timestamp: str
    pushback_type: Literal["none", "disagree", "alternative"] = "none"
    pushback_reasoning: str | None = None

    @property
    def is_pushback(self) -> bool:
        """Backward compat: True when pushback_type is not 'none'."""
        return self.pushback_type != "none"


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
    source_path: str = ""
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
    pushback_type: Literal["none", "disagree", "alternative"] = "none"
    pushback_reasoning: str | None = None

    # Backward compat: accept is_pushback from old clients
    is_pushback: bool = False


class UpdatePlanRequest(BaseModel):
    markdown: str


class AcceptRequest(BaseModel):
    save_path: str


class AppConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    access_mode: AccessMode = "local"
    ssh_destination: str = ""
    local_forward_port: int = Field(default=0, ge=0)
    public_url: str = ""

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> "AppConfig":
        if self.access_mode == "external" and not self.public_url:
            raise ValueError("public_url is required when access_mode is 'external'")
        return self
