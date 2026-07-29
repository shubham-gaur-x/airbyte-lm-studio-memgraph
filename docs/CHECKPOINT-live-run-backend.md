# Checkpoint — Dev Agent live run blocked on free-tier model quotas (2026-07-15)

**Status:** the autonomous dev-agent pipeline is **proven end-to-end except the final
model call**. Every layer works; the only open item is a model endpoint that runs a real
agentic Claude Code session within a *free* budget. Revisit when picking a backend.

## What is verified working (live, not just unit tests)

Ticket → **triage** (sprint-membership JQL, `dev-agent` label gate) → **In Progress** →
**git worktree** `agent/<KEY>` → **model routing** (backend toggle + LiteLLM alias) →
**Anthropic beta-field translation** → **auth** → **failure path** (return to To Do,
"needs human" comment, `dev_agent_runs` row, worktree cleanup). All confirmed on real
runs against Jira SCRUM board + the running containers.

## The blocker: no free model both fits and runs

Claude Code's agentic requests grow to **~68k tokens** (system prompt + tool defs +
accumulated file context). Against that:

| Backend | Result | Root cause |
|---|---|---|
| **local** qwen2.5-coder-7b @32k | loops to `max_turns`, no PR | 16GB caps useful coder at ~7B; too weak for real tickets. JIT-eviction fixed (pin main+small model). |
| **groq** llama-3.3-70b (free) | `RateLimitError` | free tier = **12k TPM**; a 68k request is 5–6× over. No free Groq model fits. |
| **gemini** 2.0-flash (free) | `429 limit: 0` | Google set free-tier `generateContent` quota to **0**; requires billing enabled (card) even for the free allowance. |
| **openrouter** (free) | **untested** | last no-card option; free Qwen2.5-Coder-32B has 128k ctx + RPM (not TPM) limits — should fit. |
| **claude** (Anthropic API) | works, **paid** | reliable; user prefers $0. |

## What is already built to support any backend (committed)

- `dev_agent/backend.py` — `DEV_AGENT_LLM_BACKEND=local|claude|openrouter|gemini|groq`,
  `resolve_backend_env`, `model_for_run` (returns LiteLLM alias for hosted), preflight.
- `litellm/config.yaml` — proxy maps `dev-agent-coder-{groq,gemini,openrouter}` aliases;
  drops Anthropic-only fields (`context_management`, `output_config`, `thinking`, …) that
  non-Anthropic providers 400 on. Proxy verified routing to Groq (`PROXY_OK`) and Gemini
  (auth OK once key loaded).
- `docker compose --profile hosted up -d litellm` starts the proxy.

## Gotchas learned (so we don't repeat them)

1. **`docker compose restart` does NOT reload `.env`** — env_file is read at *create*
   time. Use `up -d --force-recreate` after changing a key/backend in `.env`.
2. **LM Studio JIT reloads the model at default 8192 ctx** on an unknown model id —
   pin both `ANTHROPIC_MODEL` and `ANTHROPIC_SMALL_FAST_MODEL` (done in `backend.py`).
3. Newer Google AI Studio keys are ~53 chars and **do not** start with `AIza`.

## To resume the live run (pick one)

- **OpenRouter (no card):** free key at openrouter.ai → `.env`:
  `DEV_AGENT_LLM_BACKEND=openrouter`, `OPENROUTER_API_KEY=…` →
  `docker compose up -d --force-recreate litellm dev_agent` → run a `dev-agent`-labelled
  To-Do ticket. (Confirm the free model id in `litellm/config.yaml` is still offered.)
- **Gemini (enable billing, $0 under limits):** enable billing on the Google project;
  key already in `.env`; switch `DEV_AGENT_LLM_BACKEND=gemini`, force-recreate, run.
- **Anthropic (paid):** `DEV_AGENT_LLM_BACKEND=claude` + `ANTHROPIC_API_KEY`.

Test ticket used so far: **SCRUM-49** ("Add GET /ping healthcheck endpoint"), currently
To Do, `dev-agent` label removed to stop the poller looping. Re-add the label (or create a
fresh one) to run.
