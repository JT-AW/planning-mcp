"""Global plan state singleton and utilities."""

from __future__ import annotations

import json
import socket

from planning_mcp.models import PlanState

state = PlanState()

# Project/plan context for DB persistence (None = ad-hoc / legacy mode)
current_project_id: str | None = None
current_plan_id: str | None = None


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def broadcast(event_type: str, payload: dict[str, object] | None = None) -> None:
    msg = json.dumps({"type": event_type, **(payload or {})})
    with state.lock:
        for q in state.sse_subscribers:
            q.put(msg)
