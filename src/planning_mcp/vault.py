"""Vault integration — write accepted plans and sync projects to the Obsidian vault."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _vault_root() -> Path:
    return Path(os.environ.get("PLANNING_MCP_VAULT", str(Path.home() / "workspace" / "vault")))


def accept_plan_to_vault(
    project: dict[str, Any],
    plan: dict[str, Any],
    vault_domain: str | None = None,
    vault_filename: str | None = None,
) -> str:
    """Write an accepted plan to the Obsidian vault under Projects/. Returns the vault path."""
    root = _vault_root()
    project_name = project.get("name", "General")
    # Strip "Ad-hoc: " prefix if present
    if project_name.startswith("Ad-hoc: "):
        project_name = project_name[len("Ad-hoc: ") :]

    project_slug = _slugify(project_name)
    project_dir = root / "Projects" / project_slug
    project_dir.mkdir(parents=True, exist_ok=True)

    filename = vault_filename or f"cycle-{plan.get('cycle_number', 1)}-plan"
    if not filename.endswith(".md"):
        filename += ".md"

    filepath = project_dir / filename

    # Resolve areas from vault_domains (these map to Areas/ folders in the vault)
    vault_domains = project.get("vault_domains", "[]")
    if isinstance(vault_domains, str):
        vault_domains = json.loads(vault_domains)
    # If vault_domain param provided, use it; otherwise use project's vault_domains
    areas: list[str] = vault_domains if vault_domains else []
    if vault_domain and vault_domain not in areas:
        areas = [vault_domain, *areas]

    # Build frontmatter matching vault conventions
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    frontmatter_lines = [
        "---",
        "tags: [plan]",
        "status: accepted",
        f"created: {now}",
    ]
    if areas:
        if len(areas) == 1:
            frontmatter_lines.append(f"areas: {areas[0]}")
        else:
            frontmatter_lines.append("areas:")
            for area in areas:
                frontmatter_lines.append(f"  - {area}")
    frontmatter_lines.extend(["---", ""])
    frontmatter = "\n".join(frontmatter_lines)

    content = frontmatter + plan.get("markdown", "")
    filepath.write_text(content, encoding="utf-8")

    # Sync the project index note
    sync_project_to_vault(project)

    return str(filepath.relative_to(root))


def sync_project_to_vault(project: dict[str, Any]) -> None:
    """Create or update a project index note in vault/Projects/{slug}/index.md.

    This keeps the vault in sync with the DB — every project gets a note.
    """
    root = _vault_root()
    project_name = project.get("name", "General")
    if project_name.startswith("Ad-hoc: "):
        project_name = project_name[len("Ad-hoc: ") :]

    project_slug = _slugify(project_name)
    project_dir = root / "Projects" / project_slug
    project_dir.mkdir(parents=True, exist_ok=True)

    index_path = project_dir / "index.md"

    # Parse JSON fields if needed
    vault_domains = project.get("vault_domains", "[]")
    if isinstance(vault_domains, str):
        vault_domains = json.loads(vault_domains)
    vault_links = project.get("vault_links", "[]")
    if isinstance(vault_links, str):
        vault_links = json.loads(vault_links)

    status = project.get("status", "active")
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    created = project.get("created_at", now)
    if "T" in created:
        created = created.split("T")[0]

    lines = [
        "---",
        "tags: [project]",
        f"status: {status}",
        f"created: {created}",
    ]
    if vault_domains:
        lines.append(f"domains: {json.dumps(vault_domains)}")
    lines.extend(["---", "", f"# {project_name}", ""])

    if project.get("description"):
        lines.extend([project["description"], ""])

    if vault_links:
        lines.append("## Related Docs")
        for link in vault_links:
            lines.append(f"- [[{link}]]")
        lines.append("")

    index_path.write_text("\n".join(lines), encoding="utf-8")


def _slugify(text: str) -> str:
    """Convert title to a filename-safe slug."""
    slug = text.lower().strip()
    safe_chars = []
    for ch in slug:
        if ch.isalnum() or ch in ("-", "_"):
            safe_chars.append(ch)
        elif ch in (" ", "/", "\\"):
            safe_chars.append("-")
    result = "".join(safe_chars)
    while "--" in result:
        result = result.replace("--", "-")
    return result.strip("-") or "plan"
