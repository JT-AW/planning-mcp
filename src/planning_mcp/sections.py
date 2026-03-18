"""Markdown section parsing utilities."""

from __future__ import annotations

import re

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
