"""SQLite persistence layer for plans, feedback, and replies.

Connects to the shared planning-mcp database at ~/.planning-mcp/planning.db.
Uses WAL mode for concurrent read/write safety.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".planning-mcp" / "planning.db"

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def init_db() -> None:
    """Create tables if they don't exist.

    NOTE: The 'projects' table is owned by project-mcp. We create it here too
    (matching the full schema) so planning-mcp works standalone for backward compat.
    CREATE TABLE IF NOT EXISTS is safe — if project-mcp already created it, this is a no-op.
    """
    conn = _get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            parent_id TEXT REFERENCES projects(id),
            depth INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'completed', 'archived')),
            vault_domains TEXT NOT NULL DEFAULT '[]',
            vault_links TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            CHECK (depth <= 2)
        );

        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            cycle_number INTEGER NOT NULL,
            title TEXT NOT NULL,
            markdown TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'reviewing', 'accepted', 'rejected')),
            vault_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            accepted_at TEXT,
            UNIQUE (project_id, cycle_number)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES plans(id),
            type TEXT NOT NULL CHECK (type IN ('investigate', 'update_opinion', 'overall')),
            selected_text TEXT NOT NULL DEFAULT '',
            anchor_context TEXT NOT NULL DEFAULT '',
            user_message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'submitted', 'processed')),
            text_offset INTEGER NOT NULL DEFAULT -1,
            orphaned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS replies (
            id TEXT PRIMARY KEY,
            feedback_id TEXT NOT NULL REFERENCES feedback(id),
            author TEXT NOT NULL CHECK (author IN ('user', 'claude')),
            message TEXT NOT NULL,
            pushback_type TEXT NOT NULL DEFAULT 'none'
                CHECK (pushback_type IN ('none', 'disagree', 'alternative')),
            pushback_reasoning TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ── Plans ────────────────────────────────────────────────────────────────────


def create_plan(
    plan_id: str,
    project_id: str,
    cycle_number: int,
    title: str,
    markdown: str,
    status: str = "reviewing",
) -> dict[str, Any]:
    conn = _get_conn()
    now = _now()
    conn.execute(
        """INSERT INTO plans (id, project_id, cycle_number, title, markdown, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, project_id, cycle_number, title, markdown, status, now, now),
    )
    conn.commit()
    return get_plan(plan_id)  # type: ignore[return-value]


def get_plan(plan_id: str) -> dict[str, Any] | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return dict(row) if row else None


def update_plan_markdown(plan_id: str, markdown: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE plans SET markdown = ?, updated_at = ? WHERE id = ?",
        (markdown, _now(), plan_id),
    )
    conn.commit()


def update_plan_status(plan_id: str, status: str, vault_path: str | None = None) -> None:
    conn = _get_conn()
    now = _now()
    if status == "accepted":
        conn.execute(
            "UPDATE plans SET status = ?, vault_path = ?, accepted_at = ?, updated_at = ? WHERE id = ?",
            (status, vault_path, now, now, plan_id),
        )
    else:
        conn.execute(
            "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, plan_id),
        )
    conn.commit()


def list_plans(project_id: str) -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM plans WHERE project_id = ? ORDER BY cycle_number",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_next_cycle_number(project_id: str) -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(cycle_number) AS mx FROM plans WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return (row["mx"] or 0) + 1 if row else 1


# ── Feedback ─────────────────────────────────────────────────────────────────


def create_feedback(
    feedback_id: str,
    plan_id: str,
    fb_type: str,
    selected_text: str,
    anchor_context: str,
    user_message: str,
    text_offset: int = -1,
    status: str = "draft",
) -> dict[str, Any]:
    conn = _get_conn()
    now = _now()
    conn.execute(
        """INSERT INTO feedback (id, plan_id, type, selected_text, anchor_context,
           user_message, status, text_offset, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            feedback_id,
            plan_id,
            fb_type,
            selected_text,
            anchor_context,
            user_message,
            status,
            text_offset,
            now,
        ),
    )
    conn.commit()
    return {"id": feedback_id}


def list_feedback(plan_id: str) -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM feedback WHERE plan_id = ? ORDER BY created_at",
        (plan_id,),
    ).fetchall()
    result = []
    for row in rows:
        fb = dict(row)
        fb["orphaned"] = bool(fb["orphaned"])
        fb["replies"] = list_replies(fb["id"])
        result.append(fb)
    return result


def update_feedback_status(feedback_id: str, status: str) -> None:
    conn = _get_conn()
    conn.execute("UPDATE feedback SET status = ? WHERE id = ?", (status, feedback_id))
    conn.commit()


# ── Replies ──────────────────────────────────────────────────────────────────


def create_reply(
    reply_id: str,
    feedback_id: str,
    author: str,
    message: str,
    pushback_type: str = "none",
    pushback_reasoning: str | None = None,
) -> dict[str, Any]:
    conn = _get_conn()
    now = _now()
    conn.execute(
        """INSERT INTO replies (id, feedback_id, author, message, pushback_type,
           pushback_reasoning, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (reply_id, feedback_id, author, message, pushback_type, pushback_reasoning, now),
    )
    conn.commit()
    return {"id": reply_id}


def list_replies(feedback_id: str) -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM replies WHERE feedback_id = ? ORDER BY created_at",
        (feedback_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Projects (read from shared DB) ──────────────────────────────────────────


def list_projects() -> list[dict[str, Any]]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_project(project_id: str) -> dict[str, Any] | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def create_adhoc_project(project_id: str, name: str) -> dict[str, Any]:
    """Create an ad-hoc project for backward compatibility when no project is specified."""
    conn = _get_conn()
    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO projects (id, name, description, status, created_at, updated_at)
           VALUES (?, ?, '', 'active', ?, ?)""",
        (project_id, name, now, now),
    )
    conn.commit()
    return get_project(project_id)  # type: ignore[return-value]
