# planning-mcp

An MCP server for interactive plan review with browser-based annotation. Publish markdown plans to a local browser UI, annotate text with comments, iterate via threaded replies, and save accepted plans to disk.

Designed for use with Claude Code's plan mode workflow.

## Quick Start

### Installation

```bash
# Install with uv
uv pip install -e .

### MCP Server Registration

Add to your MCP settings (`~/.claude/settings.json` or project-level):

```json
{
  "mcpServers": {
    "planning-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/planning-mcp", "planning-mcp"]
    }
  }
}
```

### CLAUDE.md Setup

Claude needs instructions to orchestrate the planning workflow. Add the following to your `CLAUDE.md` (global or project-level), adapting the vault path and save conventions to your setup:

<details>
<summary><strong>Copy-paste CLAUDE.md block</strong></summary>

```markdown
## Plan Mode

This section governs all planning activity. It uses **planning-mcp** for browser-based plan review.

### When Plan Mode Applies

**MANDATORY: Every plan must be presented through the planning-mcp browser UI via `open_plan`.**
Never present plans inline in the conversation. No exceptions — not for "simple" plans, not for
quick summaries. If you have a plan, it goes through the planning-mcp.

**MANDATORY: One plan per file.** If the scope changes significantly during a conversation, write
a new plan file rather than appending to the existing one.

- **Always** for feature work, architecture changes, investigation strategies, multi-file refactors
- **Always** when the user explicitly enters plan mode or asks "let's plan X"
- For trivial single-file bug fixes or one-liner changes, planning is optional. But if you find
  yourself writing more than a sentence of strategy, open the planning-mcp.

### Phase 1: Context Loading

When plan mode begins, gather context before composing:

1. **Load relevant context** from the codebase or any reference docs.
2. **Check for prior plans** in your designated plan storage directory.

### Phase 2: Plan Composition

1. Compose the plan with clear sections (Objective, Approach, Implementation Sequence, etc.).
2. **Write to the plan file** — the path provided by plan mode (e.g., `~/.claude/plans/<file>.md`).
   Never inline the full plan content in tool call payloads.
3. **Publish the same file** to the browser:
   `open_plan(plan_file=<plan_file_path>, plan_title="<title>")`.
   This ensures the browser renders the exact same markdown the plan agent shows in its
   approval modal.
4. Iterate via browser annotations — `update_section` / `update_plan` write back to the same file.
5. **Before calling `ExitPlanMode`**, ensure the plan is visible in the browser. If the plan is
   already open, use `reply_to_feedback` / `update_section` to address comments — do NOT call
   `open_plan` again, as that clears the comment history.

### Phase 3: Feedback Loop

1. **Poll** `get_feedback()` for user annotations from the browser.
2. **Respond** to feedback:
   - `reply_to_feedback(feedback_id, message, pushback_type)` — push back with `"disagree"` or
     `"alternative"` when warranted
   - `update_section(section_title, new_content)` — surgical update (preferred, preserves
     comment anchors)
   - `update_plan(plan_file=<plan_file_path>)` — full replace only for major restructuring
   - `mark_feedback_processed(feedback_id)` — mark handled items
3. **Repeat** until the user is satisfied.

### Phase 4: Acceptance

**MANDATORY: Always save accepted plans to `vault/Projects/{project-name}/{slug}-plan.md`.**
The `{project-name}` folder maps to the work area (create it if it doesn't exist). The `{slug}`
is a kebab-case descriptor of the plan content. Never save plans to `/tmp/` or other ad-hoc
locations.

1. Call `accept_plan(save_path="vault/Projects/{project-name}/{slug}-plan.md")` — saves the plan
   with YAML frontmatter and a Review Comments section containing all feedback and replies.
2. The plan itself should reference any domain guides or docs it was built from.
```

</details>

**Customization notes:**
- **Vault path**: Replace `vault/Projects/` with your own plan storage convention. `accept_plan` writes wherever you specify — no built-in default.
- **Test-first planning**: Add a rule that plan structure must be Objective → Test Design → Implementation Sequence.
- **Red-teaming**: Add a Phase 2 step to optionally red-team domain-heavy plans with a companion LLM before publishing.
- **Plan addendums**: Add a Phase 4.5 rule that when an accepted plan changes during implementation, an addendum is appended to the vault file automatically.

## Architecture

The server runs over stdio (MCP protocol) and lazily starts a local web server when `open_plan` is first called.

```
Claude Code (stdio)          Browser (HTTP + SSE)
      |                            |
      |--- MCP tools ------------->|
      |    open_plan               |--- GET /plan
      |    get_feedback            |--- GET /feedback/all
      |    reply_to_feedback       |--- POST /feedback
      |    update_section          |--- POST /feedback/submit-all
      |    update_plan             |--- GET /events (SSE)
      |    mark_feedback_processed |
      |    accept_plan             |--- POST /accept
```

**Key design**: everything is in-memory during the session. Zero disk I/O until you call `accept_plan`. No database, no persistent state between sessions.

### Source Files

| File | Purpose |
|------|---------|
| `tools.py` | 7 MCP tool definitions |
| `web.py` | FastAPI routes, SSE endpoint, uvicorn lifecycle |
| `models.py` | Pydantic/dataclass models for feedback, replies, state |
| `reanchor.py` | Comment re-anchoring when plan text changes |
| `sections.py` | Markdown section parsing for surgical updates |
| `state.py` | Global state singleton + SSE broadcast utility |
| `static/` | Browser UI (vanilla JS, marked.js, DOMPurify) |

### Dependencies

- `mcp` — FastMCP server framework
- `fastapi` + `uvicorn` — Local web server for the browser UI
- `pydantic` — Request/response validation

## MCP Tools

### `open_plan`

Publish a plan to the browser for interactive review. Starts the web server on first call, opens the browser automatically.

```
open_plan(plan_file="/tmp/plan.md", plan_title="My Plan")
# Returns: {"port": 59153, "url": "http://127.0.0.1:59153"}
```

**Prefer `plan_file` over `plan_markdown`** to keep tool call payloads small. Write markdown to a temp file first.

### `get_feedback`

Returns all submitted (not draft/processed) feedback items from the browser. Each item includes:
- `id`, `type` ("investigate" | "update_opinion" | "overall")
- `selected_text`, `anchor_context`, `text_offset`
- `user_message`, `timestamp`, `replies[]`

### `reply_to_feedback`

Reply to a user's feedback comment. Appears as a threaded reply in the browser margin. Claude's replies are rendered as markdown.

```
reply_to_feedback(
    feedback_id="...",
    message="**Agreed** — I'll update the section.",
    pushback_type="none"  # or "disagree", "alternative"
)
```

### `update_section`

Surgically update a single section of the plan by header title. Preserves comment anchors better than full replacement.

```
update_section(section_title="Phase 2: Composition", new_content="...")
```

### `update_plan`

Replace the entire plan markdown. Browser auto-refreshes via SSE. Comments are re-anchored to their original text positions.

```
update_plan(plan_file="/tmp/plan-v2.md")
```

### `mark_feedback_processed`

Mark a feedback item as handled. The comment card collapses in the browser with a "resolved" badge. If the user replies to a processed comment, it reopens and reappears in `get_feedback`.

### `accept_plan`

Save the current plan to a file with YAML frontmatter. Appends a `## Review Comments` section with all feedback and threaded replies.

```
accept_plan(save_path="/path/to/vault/Projects/My-Project/feature-plan.md")
```

Output format:
```yaml
---
title: "Plan Title"
tags: [plan]
status: accepted
created: 2026-03-19
---

# Plan content...

---

## Review Comments

### Investigate
> selected text
User's comment
**Claude:** Reply with markdown
```

## Browser UI

The browser UI opens automatically when `open_plan` is called. It provides:

### Annotation

Select text in the plan and choose **Investigate** (amber highlight) or **Update/Opinion** (blue highlight) from the toolbar. Add a comment describing what should change.

### Comment Cards

Comments appear in the right margin, positioned near their anchored text:
- **Draft** — dashed border, editing in progress
- **Submitted** — solid border, waiting for Claude's response
- **Processed** — collapsed with "resolved" badge, expandable
- **Orphaned** — dashed amber border when plan text changes

### Threaded Replies

Claude's replies appear in comment threads with markdown rendering. Users can reply back. Replying to a processed (collapsed) comment reopens it.

### Discard

Hover over any comment card to reveal the `x` button. Discarding removes both the comment card and its text highlight.

### Real-Time Updates

The browser connects via Server-Sent Events (SSE). When Claude calls `update_section`, `reply_to_feedback`, or `mark_feedback_processed`, the browser updates instantly without requiring a page refresh.

## Workflow

The intended workflow integrates with Claude Code's plan mode:

### 1. Compose

Claude writes the plan to the plan file (e.g., `~/.claude/plans/<file>.md`) and publishes it to the browser:

```
open_plan(plan_file="~/.claude/plans/my-plan.md", plan_title="Feature Plan")
```

Both the browser and Claude Code's plan approval modal render the same file.

### 2. Annotate

The user reviews in the browser, highlighting text and adding comments. They click **Submit & Revise** to send all feedback to Claude.

### 3. Iterate

Claude polls with `get_feedback()`, responds with `reply_to_feedback()`, and updates the plan with `update_section()` or `update_plan()`. Processed comments collapse automatically.

### 4. Accept

Once satisfied, Claude calls `accept_plan(save_path="...")` to persist the plan with review comments. The saved file includes YAML frontmatter and the full discussion thread.

### Vault Integration

When used with an Obsidian vault, accepted plans are saved to:

```
vault/Projects/{project-name}/{slug}-plan.md
```

The `{project-name}` folder maps to the work area. The `{slug}` is a kebab-case descriptor (e.g., `auth-refactor-plan.md`).

## Development

```bash
# Install dev dependencies
uv sync --dev

# Lint
uv run ruff check src/
uv run ruff format src/

# Type check
uv run mypy src/
```
