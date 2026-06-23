# AGENTS.md — airbyte-lm-studio-memgraph

This file maps intents to skills for non-Claude-Code agents (Codex, Gemini CLI, Cursor).
Claude Code users: see CLAUDE.md and use /architect, /plan, /build, /review, /ship.

## Intent → Skill Mapping

| Intent | Skill | Notes |
|---|---|---|
| Starting a new feature | brainstorming → writing-plans | Always spec before code |
| Implementing a task | subagent-driven-development | One task at a time |
| Writing a test | test-driven-development | RED first, always |
| Bug investigation | systematic-debugging | 4-phase root cause |
| Code review | requesting-code-review | Before any merge |
| Finishing a branch | finishing-a-development-branch | Verify tests first |

## Project Context

Read CLAUDE.md before any task. Key facts:
- LM Studio (local) is the LLM — endpoint http://host.docker.internal:1234/v1
- Memgraph runs in Docker locally — bolt://localhost:7687
- All graph writes use MERGE and ACID transactions
- No cloud services (no Render, no Groq, no Memgraph Cloud, no Neon)
- v3 repo (shubham-gaur-x/airbyte-meeting) is read-only reference — never modify it
