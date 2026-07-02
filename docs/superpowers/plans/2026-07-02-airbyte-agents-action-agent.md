# Airbyte Agents Action Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Non-engineering `meeting-action-item` Jira tickets get automatically worked: graph context pulled, deliverable drafted by LM Studio, posted as a comment via the Airbyte Agent SDK, ticket moved To Do → In Review.

**Architecture:** One new module `transform_service/action_agent.py` (the ONLY home of `airbyte-agent-sdk` usage) running inside the existing transform_service process. Fixed 6-step pipeline per ticket: find → marker guard → graph context → LM draft → comment → transition. Triggered by APScheduler (5 min), the Airbyte webhook background task, and a manual endpoint.

**Tech Stack:** Python 3.11, `airbyte-agent-sdk` (hosted mode), LM Studio via existing `extractor._get_client()`, `memory_retrieval.full_memory_query`, FastAPI/APScheduler wiring in `main.py`, pytest + anyio with mocks.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-02-airbyte-agents-action-agent-design.md`
- `airbyte-agent-sdk` calls ONLY in `transform_service/action_agent.py`
- No Jira REST calls in this module — REST stays in `jira_client.py`; this module uses the SDK exclusively
- `action_agent` is NEVER called from `graph_builder.py` (it consumes `memory_retrieval` — query-time only)
- All LM Studio access via `extractor._get_client()` — no new `AsyncOpenAI` instances
- Type hints on ALL function signatures; Pydantic v2 where models are needed
- `structlog` logging; every log includes `step`
- `@with_retry(max_attempts=3, base_delay=2.0)` on external (SDK) calls
- Terminal state the agent may set: **In Review**. Never Done.
- `ACTION_AGENT_MARKER = "[action-agent draft]"` — never change once shipped
- Commit messages: plain conventional style; never mention AI tools
- Existing suite is 101 tests and must stay green
- Env names: `AIRBYTE_AGENTS_CLIENT_ID`, `AIRBYTE_AGENTS_CLIENT_SECRET`, `AIRBYTE_AGENTS_CONNECTOR_ID`, `ACTION_AGENT_ENABLED`, `ACTION_AGENT_BATCH_SIZE` (the `AIRBYTE_CLIENT_ID`/`SECRET` pair already in `.env` belongs to Airbyte Cloud ELT — do not touch or reuse)

---

### Task 1: SDK dependency, config plumbing, connectivity probe

**Files:**
- Modify: `transform_service/requirements.txt`
- Modify: `.env.example` (after the Airbyte section)
- Create: `scripts/test_action_agent_sdk.py`

**Interfaces:**
- Produces: `airbyte-agent-sdk` importable inside the transform_service container; env var names used by all later tasks; a manual probe script used again in Task 6.

- [ ] **Step 1: Add the dependency**

Append to `transform_service/requirements.txt`:

```
airbyte-agent-sdk
```

- [ ] **Step 2: Add env vars to `.env.example`**

After the existing `AIRBYTE_WEBHOOK_SECRET=` line, add:

```env
# Action Agent (Airbyte Agents SDK — app.airbyte.ai, org Onix; different product
# from the AIRBYTE_CLIENT_ID/SECRET pair above, which is Airbyte Cloud ELT)
ACTION_AGENT_ENABLED=true
ACTION_AGENT_BATCH_SIZE=5
AIRBYTE_AGENTS_CLIENT_ID=
AIRBYTE_AGENTS_CLIENT_SECRET=
AIRBYTE_AGENTS_CONNECTOR_ID=
```

Do NOT edit `.env` — the user adds real credentials there directly (never through chat).

- [ ] **Step 3: Write the connectivity probe script**

Create `scripts/test_action_agent_sdk.py`:

```python
"""Manual probe: verify Airbyte Agent SDK credentials and Jira connector.

Run after configuring the Jira connector and SDK credentials in app.airbyte.ai:
  docker compose exec transform_service python scripts/test_action_agent_sdk.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

from airbyte_agent_sdk.connectors.jira import JiraConnector
from airbyte_agent_sdk.types import AirbyteAuthConfig


async def main() -> None:
    auth = AirbyteAuthConfig(
        airbyte_client_id=os.environ["AIRBYTE_AGENTS_CLIENT_ID"],
        airbyte_client_secret=os.environ["AIRBYTE_AGENTS_CLIENT_SECRET"],
        connector_id=os.environ["AIRBYTE_AGENTS_CONNECTOR_ID"],
    )
    project = os.environ.get("JIRA_PROJECT_KEY", "SCRUM")
    async with JiraConnector(auth_config=auth) as jira:
        result = await jira.issues.api_search(
            jql=f'project = "{project}" ORDER BY created DESC',
            max_results=3,
            fields="summary,status,labels",
        )
        records = getattr(result, "data", result)
        print("SDK connectivity OK. Sample issues:")
        for r in list(records)[:3]:
            print(" -", r)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Rebuild the container and verify the import**

```bash
docker compose build transform_service && docker compose up -d transform_service
docker compose exec transform_service python -c "from airbyte_agent_sdk.connectors.jira import JiraConnector; print('import ok')"
```

Expected: `import ok`. (The probe itself needs real credentials — that's Task 6.)

If the `AirbyteAuthConfig` import path or constructor kwargs differ in the installed SDK version, check with
`docker compose exec transform_service python -c "import airbyte_agent_sdk, inspect; print(inspect.signature(airbyte_agent_sdk.connectors.jira.JiraConnector.__init__))"`
and adjust the probe AND `action_agent.py` (Task 2) to match — then record the actual signature in the commit message body.

- [ ] **Step 5: Run existing suite to confirm no breakage, then commit**

```bash
docker compose exec -w /app transform_service python -m pytest tests/ -q
```

Expected: `101 passed`

```bash
git add transform_service/requirements.txt .env.example scripts/test_action_agent_sdk.py
git commit -m "feat: add airbyte-agent-sdk dependency and connectivity probe"
```

---

### Task 2: `action_agent.py` — constants, connector factory, pure helpers, eligibility search, marker guard

**Files:**
- Create: `transform_service/action_agent.py`
- Create: `tests/test_phase27_action_agent.py`

**Interfaces:**
- Consumes: env vars from Task 1.
- Produces (used by Tasks 3–5):
  - `ACTION_AGENT_MARKER: str = "[action-agent draft]"`
  - `_make_connector() -> JiraConnector` (async-context-manager instance, not yet entered)
  - `_records(resp) -> list` (normalizes SDK envelope/dict/list)
  - `_adf_to_text(node) -> str` (flattens Atlassian Document Format to plain text)
  - `async find_eligible_tickets(jira) -> list[dict]` — returns `[{"key": str, "summary": str, "description": str}]`
  - `async has_agent_draft(jira, issue_key: str) -> bool`

- [ ] **Step 1: Write failing tests for the pure helpers and search/guard**

Create `tests/test_phase27_action_agent.py`:

```python
"""Phase 27: Tests for transform_service/action_agent.py."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import action_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_record(key: str, summary: str, description=None):
    return {"key": key, "fields": {"summary": summary, "description": description}}


def _mock_jira(search_records=None, comments=None):
    jira = MagicMock()
    search_resp = MagicMock()
    search_resp.data = search_records or []
    jira.issues.api_search = AsyncMock(return_value=search_resp)
    comments_resp = MagicMock()
    comments_resp.data = comments or []
    jira.issue_comments.list = AsyncMock(return_value=comments_resp)
    jira.issue_comments.create = AsyncMock()
    transitions_resp = MagicMock()
    transitions_resp.data = [
        {"id": "21", "name": "In Progress", "to": {"name": "In Progress"}},
        {"id": "31", "name": "In Review", "to": {"name": "In Review"}},
    ]
    jira.issue_transitions.list = AsyncMock(return_value=transitions_resp)
    jira.issue_transitions.create = AsyncMock()
    return jira


# ---------------------------------------------------------------------------
# _adf_to_text / _records
# ---------------------------------------------------------------------------

def test_adf_to_text_flattens_nested_document():
    adf = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "world"}]},
        ],
    }
    assert action_agent._adf_to_text(adf) == "Hello world"


def test_adf_to_text_passes_plain_string_through():
    assert action_agent._adf_to_text("already plain") == "already plain"


def test_adf_to_text_handles_none():
    assert action_agent._adf_to_text(None) == ""


def test_records_prefers_data_attribute():
    resp = MagicMock()
    resp.data = [1, 2]
    assert action_agent._records(resp) == [1, 2]


def test_records_handles_plain_list():
    assert action_agent._records([{"key": "A-1"}]) == [{"key": "A-1"}]


# ---------------------------------------------------------------------------
# find_eligible_tickets
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_find_eligible_tickets_builds_bounded_jql_and_maps_fields():
    jira = _mock_jira(search_records=[
        _search_record("SCRUM-47", "Action requested", {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "details"}]}],
        }),
    ])
    with patch.dict(os.environ, {"JIRA_PROJECT_KEY": "SCRUM", "ACTION_AGENT_BATCH_SIZE": "5"}):
        tickets = await action_agent.find_eligible_tickets(jira)

    assert tickets == [{"key": "SCRUM-47", "summary": "Action requested", "description": "details"}]
    jql = jira.issues.api_search.call_args.kwargs["jql"]
    assert 'project = "SCRUM"' in jql
    assert 'status = "To Do"' in jql
    assert 'labels = "meeting-action-item"' in jql


@pytest.mark.anyio
async def test_find_eligible_tickets_respects_batch_size():
    records = [_search_record(f"SCRUM-{i}", f"t{i}") for i in range(10)]
    jira = _mock_jira(search_records=records)
    with patch.dict(os.environ, {"ACTION_AGENT_BATCH_SIZE": "3"}):
        tickets = await action_agent.find_eligible_tickets(jira)
    assert len(tickets) == 3


# ---------------------------------------------------------------------------
# has_agent_draft
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_has_agent_draft_true_when_marker_comment_exists():
    jira = _mock_jira(comments=[
        {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": action_agent.ACTION_AGENT_MARKER}]},
        ]}},
    ])
    assert await action_agent.has_agent_draft(jira, "SCRUM-47") is True


@pytest.mark.anyio
async def test_has_agent_draft_false_when_no_marker():
    jira = _mock_jira(comments=[
        {"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "human comment"}]},
        ]}},
    ])
    assert await action_agent.has_agent_draft(jira, "SCRUM-47") is False
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose cp tests/test_phase27_action_agent.py transform_service:/app/tests/
docker compose exec -w /app transform_service python -m pytest tests/test_phase27_action_agent.py -q
```

Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (module doesn't exist yet).

- [ ] **Step 3: Implement the module skeleton**

Create `transform_service/action_agent.py`:

```python
"""Action agent — works non-engineering meeting action items in Jira.

The ONLY module allowed to use airbyte-agent-sdk. Pipeline per ticket:
find -> marker guard -> graph context -> LM draft -> comment -> To Do->In Review.

Never call this module from graph_builder.py: it consumes memory_retrieval,
which is query-time only (see CLAUDE.md).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import structlog

from airbyte_agent_sdk.connectors.jira import JiraConnector
from airbyte_agent_sdk.types import AirbyteAuthConfig

from transform_service.utils import with_retry

log = structlog.get_logger()

ACTION_AGENT_MARKER = "[action-agent draft]"
_LABEL = "meeting-action-item"
_REVIEW_STATUS = "In Review"


def _make_connector() -> JiraConnector:
    """Build (do not enter) the hosted-mode Jira connector.

    Credentials are passed explicitly — the SDK's env autodiscovery reads
    AIRBYTE_CLIENT_ID/SECRET, which belong to the Airbyte Cloud ELT API here.
    """
    return JiraConnector(
        auth_config=AirbyteAuthConfig(
            airbyte_client_id=os.environ["AIRBYTE_AGENTS_CLIENT_ID"],
            airbyte_client_secret=os.environ["AIRBYTE_AGENTS_CLIENT_SECRET"],
            connector_id=os.environ["AIRBYTE_AGENTS_CONNECTOR_ID"],
        )
    )


def _records(resp: Any) -> list:
    """Normalize an SDK response envelope to a list of records."""
    data = getattr(resp, "data", None)
    if data is not None:
        return list(data)
    if isinstance(resp, dict):
        return list(resp.get("records") or resp.get("issues") or [])
    if isinstance(resp, list):
        return resp
    return []


def _adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    parts: list[str] = []
    if isinstance(node, dict):
        text = node.get("text")
        if isinstance(text, str):
            parts.append(text)
        for child in node.get("content") or []:
            child_text = _adf_to_text(child)
            if child_text:
                parts.append(child_text)
    elif isinstance(node, list):
        for child in node:
            child_text = _adf_to_text(child)
            if child_text:
                parts.append(child_text)
    return " ".join(parts)


@with_retry(max_attempts=3, base_delay=2.0)
async def find_eligible_tickets(jira: JiraConnector) -> list[dict]:
    """To Do tickets labeled meeting-action-item, capped at batch size."""
    project = os.environ.get("JIRA_PROJECT_KEY", "SCRUM")
    batch = int(os.environ.get("ACTION_AGENT_BATCH_SIZE", "5"))
    jql = (
        f'project = "{project}" AND status = "To Do" '
        f'AND labels = "{_LABEL}" ORDER BY created ASC'
    )
    resp = await jira.issues.api_search(
        jql=jql, max_results=batch, fields="summary,description,status,labels"
    )
    tickets: list[dict] = []
    for rec in _records(resp)[:batch]:
        fields = rec.get("fields") or {}
        tickets.append({
            "key": rec.get("key", ""),
            "summary": fields.get("summary") or "",
            "description": _adf_to_text(fields.get("description")),
        })
    log.info("action_agent.eligible", step="find", count=len(tickets))
    return tickets


@with_retry(max_attempts=3, base_delay=2.0)
async def has_agent_draft(jira: JiraConnector, issue_key: str) -> bool:
    """True if a previous run already posted the marker comment."""
    resp = await jira.issue_comments.list(issue_id_or_key=issue_key)
    for comment in _records(resp):
        body = comment.get("body") if isinstance(comment, dict) else None
        if ACTION_AGENT_MARKER in _adf_to_text(body):
            return True
    return False
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose cp transform_service/action_agent.py transform_service:/app/transform_service/
docker compose exec -w /app transform_service python -m pytest tests/test_phase27_action_agent.py -q
```

Expected: all Task 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add transform_service/action_agent.py tests/test_phase27_action_agent.py
git commit -m "feat: action agent eligibility search and idempotency guard"
```

---

### Task 3: Graph context + LM Studio draft

**Files:**
- Modify: `transform_service/action_agent.py` (append)
- Modify: `tests/test_phase27_action_agent.py` (append)

**Interfaces:**
- Consumes: `memory_retrieval.full_memory_query(question: str) -> dict` (keys: `answer`, `session_id`, `nodes_used`, `context_summary`), `extractor._get_client()`.
- Produces (used by Task 4):
  - `async build_context(summary: str, description: str) -> tuple[str, int]` — `(context_text, nodes_count)`; never raises
  - `async draft_deliverable(summary: str, description: str, context_text: str) -> Optional[str]` — deliverable text or None on any failure; never raises

- [ ] **Step 1: Write failing tests**

Append to `tests/test_phase27_action_agent.py`:

```python
# ---------------------------------------------------------------------------
# build_context / draft_deliverable
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_build_context_uses_full_memory_query():
    fake = AsyncMock(return_value={
        "answer": "Matteo hosts the migration meetings.",
        "session_id": "s1",
        "nodes_used": [{"id": "a"}, {"id": "b"}],
        "context_summary": {"people_found": 1, "topics_found": 0},
    })
    with patch("transform_service.action_agent.memory_retrieval.full_memory_query", fake):
        text, count = await action_agent.build_context("Follow up with Matteo", "reschedule")

    assert "Matteo hosts" in text
    assert count == 2
    question = fake.call_args.args[0]
    assert "Follow up with Matteo" in question


@pytest.mark.anyio
async def test_build_context_survives_memory_failure():
    fake = AsyncMock(side_effect=RuntimeError("graph down"))
    with patch("transform_service.action_agent.memory_retrieval.full_memory_query", fake):
        text, count = await action_agent.build_context("t", "d")
    assert text == ""
    assert count == 0


@pytest.mark.anyio
async def test_draft_deliverable_returns_text():
    msg = MagicMock()
    msg.message.content = "Hi Matteo, could we move our sync to Thursday?"
    resp = MagicMock()
    resp.choices = [msg]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    with (
        patch("transform_service.action_agent._get_client", return_value=client),
        patch.dict(os.environ, {"LM_STUDIO_MODEL": "test-model"}),
    ):
        draft = await action_agent.draft_deliverable("Follow up", "reschedule", "context")
    assert "Thursday" in draft


@pytest.mark.anyio
async def test_draft_deliverable_returns_none_on_llm_failure():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("LM down"))
    with (
        patch("transform_service.action_agent._get_client", return_value=client),
        patch.dict(os.environ, {"LM_STUDIO_MODEL": "test-model"}),
    ):
        draft = await action_agent.draft_deliverable("t", "d", "c")
    assert draft is None


@pytest.mark.anyio
async def test_draft_deliverable_returns_none_on_empty_answer():
    msg = MagicMock()
    msg.message.content = "   "
    resp = MagicMock()
    resp.choices = [msg]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    with (
        patch("transform_service.action_agent._get_client", return_value=client),
        patch.dict(os.environ, {"LM_STUDIO_MODEL": "test-model"}),
    ):
        draft = await action_agent.draft_deliverable("t", "d", "c")
    assert draft is None
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose cp tests/test_phase27_action_agent.py transform_service:/app/tests/
docker compose exec -w /app transform_service python -m pytest tests/test_phase27_action_agent.py -q
```

Expected: new tests FAIL (`AttributeError: ... has no attribute 'build_context'`).

- [ ] **Step 3: Implement**

Append to `transform_service/action_agent.py` (and extend the module's imports with `from transform_service import memory_retrieval` and `from transform_service.extractor import _get_client`):

```python
_DRAFT_SYSTEM_PROMPT = """You draft deliverables for meeting action items.
Given a Jira ticket and knowledge-graph context about the people, meetings,
and facts involved, write the concrete deliverable the ticket asks for —
the actual follow-up message, document summary, plan, or answer.
Be specific: use names, dates, and facts from the context when available.
If context is empty, draft from the ticket alone.
Output ONLY the deliverable text. No preamble, no meta-commentary."""


async def build_context(summary: str, description: str) -> tuple[str, int]:
    """Graph context for a ticket via the sanctioned query-time interface.

    Returns (context_text, nodes_count). Never raises — empty context on failure.
    """
    question = (
        f"Context for this action item: {summary}. {description} "
        "Who and what is involved, and what should the deliverable contain?"
    )
    try:
        result = await memory_retrieval.full_memory_query(question)
        return result.get("answer") or "", len(result.get("nodes_used") or [])
    except Exception as exc:
        log.warning("action_agent.context_failed", step="context", error=str(exc))
        return "", 0


async def draft_deliverable(
    summary: str, description: str, context_text: str
) -> Optional[str]:
    """One LM Studio call producing the deliverable. None on any failure."""
    client = _get_client()
    user_prompt = (
        f"Ticket: {summary}\n\nDetails: {description or '(none)'}\n\n"
        f"Knowledge graph context:\n{context_text or '(none available)'}"
    )
    try:
        resp = await client.chat.completions.create(
            model=os.environ["LM_STUDIO_MODEL"],
            messages=[
                {"role": "system", "content": _DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=800,
        )
        draft = (resp.choices[0].message.content or "").strip()
        return draft or None
    except Exception as exc:
        log.warning("action_agent.draft_failed", step="draft", error=str(exc))
        return None
```

- [ ] **Step 4: Run tests to verify pass**

```bash
docker compose cp transform_service/action_agent.py transform_service:/app/transform_service/
docker compose exec -w /app transform_service python -m pytest tests/test_phase27_action_agent.py -q
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add transform_service/action_agent.py tests/test_phase27_action_agent.py
git commit -m "feat: action agent graph context and deliverable drafting"
```

---

### Task 4: Comment posting, transition, orchestrator

**Files:**
- Modify: `transform_service/action_agent.py` (append)
- Modify: `tests/test_phase27_action_agent.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2–3.
- Produces (used by Task 5):
  - `async post_draft(jira, issue_key: str, draft: str, nodes_count: int) -> None`
  - `async transition_to_review(jira, issue_key: str) -> bool`
  - `async process_action_items() -> dict` — `{"considered": int, "drafted": int, "repaired": int, "failed": int}` or `{"skipped": str}`; never raises

- [ ] **Step 1: Write failing tests**

Append to `tests/test_phase27_action_agent.py`:

```python
# ---------------------------------------------------------------------------
# post_draft / transition_to_review / process_action_items
# ---------------------------------------------------------------------------

def _enabled_env():
    return patch.dict(os.environ, {
        "ACTION_AGENT_ENABLED": "true",
        "AIRBYTE_AGENTS_CLIENT_ID": "cid",
        "AIRBYTE_AGENTS_CLIENT_SECRET": "cs",
        "AIRBYTE_AGENTS_CONNECTOR_ID": "conn",
        "JIRA_PROJECT_KEY": "SCRUM",
        "LM_STUDIO_MODEL": "test-model",
    })


def _patch_connector(jira):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=jira)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("transform_service.action_agent._make_connector", return_value=ctx)


@pytest.mark.anyio
async def test_post_draft_includes_marker_and_footer():
    jira = _mock_jira()
    await action_agent.post_draft(jira, "SCRUM-47", "The deliverable.", 4)
    kwargs = jira.issue_comments.create.call_args.kwargs
    assert kwargs["issue_id_or_key"] == "SCRUM-47"
    body_text = action_agent._adf_to_text(kwargs["body"])
    assert action_agent.ACTION_AGENT_MARKER in body_text
    assert "The deliverable." in body_text
    assert "4 nodes" in body_text


@pytest.mark.anyio
async def test_transition_to_review_picks_in_review_id():
    jira = _mock_jira()
    ok = await action_agent.transition_to_review(jira, "SCRUM-47")
    assert ok is True
    kwargs = jira.issue_transitions.create.call_args.kwargs
    assert kwargs["issue_id_or_key"] == "SCRUM-47"
    assert kwargs["transition"] == {"id": "31"}


@pytest.mark.anyio
async def test_transition_to_review_false_when_no_matching_transition():
    jira = _mock_jira()
    resp = MagicMock()
    resp.data = [{"id": "41", "name": "Done", "to": {"name": "Done"}}]
    jira.issue_transitions.list = AsyncMock(return_value=resp)
    ok = await action_agent.transition_to_review(jira, "SCRUM-47")
    assert ok is False
    jira.issue_transitions.create.assert_not_called()


@pytest.mark.anyio
async def test_process_happy_path_drafts_comments_and_transitions():
    jira = _mock_jira(search_records=[_search_record("SCRUM-47", "Follow up", None)])
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.build_context", AsyncMock(return_value=("ctx", 2))),
        patch("transform_service.action_agent.draft_deliverable", AsyncMock(return_value="draft text")),
    ):
        result = await action_agent.process_action_items()

    assert result["drafted"] == 1
    assert result["failed"] == 0
    jira.issue_comments.create.assert_called_once()
    jira.issue_transitions.create.assert_called_once()


@pytest.mark.anyio
async def test_process_marker_present_repairs_transition_without_second_comment():
    jira = _mock_jira(
        search_records=[_search_record("SCRUM-47", "Follow up", None)],
        comments=[{"body": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": action_agent.ACTION_AGENT_MARKER}]},
        ]}}],
    )
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.draft_deliverable", AsyncMock()) as mock_draft,
    ):
        result = await action_agent.process_action_items()

    assert result["repaired"] == 1
    mock_draft.assert_not_called()
    jira.issue_comments.create.assert_not_called()
    jira.issue_transitions.create.assert_called_once()


@pytest.mark.anyio
async def test_process_llm_failure_writes_nothing():
    jira = _mock_jira(search_records=[_search_record("SCRUM-47", "Follow up", None)])
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.build_context", AsyncMock(return_value=("", 0))),
        patch("transform_service.action_agent.draft_deliverable", AsyncMock(return_value=None)),
    ):
        result = await action_agent.process_action_items()

    assert result["failed"] == 1
    assert result["drafted"] == 0
    jira.issue_comments.create.assert_not_called()
    jira.issue_transitions.create.assert_not_called()


@pytest.mark.anyio
async def test_process_one_bad_ticket_does_not_abort_batch():
    jira = _mock_jira(search_records=[
        _search_record("SCRUM-1", "bad", None),
        _search_record("SCRUM-2", "good", None),
    ])
    drafts = AsyncMock(side_effect=[RuntimeError("boom"), "fine"])
    with (
        _enabled_env(),
        _patch_connector(jira),
        patch("transform_service.action_agent.build_context", AsyncMock(return_value=("", 0))),
        patch("transform_service.action_agent.draft_deliverable", drafts),
    ):
        result = await action_agent.process_action_items()

    assert result["failed"] == 1
    assert result["drafted"] == 1


@pytest.mark.anyio
async def test_process_disabled_is_noop():
    with patch.dict(os.environ, {"ACTION_AGENT_ENABLED": "false"}):
        result = await action_agent.process_action_items()
    assert result == {"skipped": "disabled"}


@pytest.mark.anyio
async def test_process_missing_credentials_is_noop():
    env = {"ACTION_AGENT_ENABLED": "true", "AIRBYTE_AGENTS_CLIENT_ID": ""}
    with patch.dict(os.environ, env):
        result = await action_agent.process_action_items()
    assert result == {"skipped": "no_credentials"}
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose cp tests/test_phase27_action_agent.py transform_service:/app/tests/
docker compose exec -w /app transform_service python -m pytest tests/test_phase27_action_agent.py -q
```

Expected: new tests FAIL.

- [ ] **Step 3: Implement**

Append to `transform_service/action_agent.py`:

```python
def _draft_comment_body(draft: str, nodes_count: int) -> dict:
    """ADF document: marker paragraph, deliverable, provenance footer."""
    footer = (
        "— Drafted by action_agent (LM Studio, local) via Airbyte Agent SDK. "
        f"Graph context: {nodes_count} nodes consulted."
    )
    paragraphs = [ACTION_AGENT_MARKER, draft, footer]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
            for text in paragraphs
        ],
    }


@with_retry(max_attempts=3, base_delay=2.0)
async def post_draft(
    jira: JiraConnector, issue_key: str, draft: str, nodes_count: int
) -> None:
    """Post the drafted deliverable as a marker-prefixed comment."""
    await jira.issue_comments.create(
        issue_id_or_key=issue_key,
        body=_draft_comment_body(draft, nodes_count),
    )
    log.info("action_agent.draft_posted", step="comment", issue_key=issue_key)


@with_retry(max_attempts=3, base_delay=2.0)
async def transition_to_review(jira: JiraConnector, issue_key: str) -> bool:
    """Move the ticket to In Review. False if no such transition exists."""
    resp = await jira.issue_transitions.list(issue_id_or_key=issue_key)
    transition_id: Optional[str] = None
    for t in _records(resp):
        target = ((t.get("to") or {}).get("name")) or t.get("name")
        if target == _REVIEW_STATUS:
            transition_id = t.get("id")
            break
    if transition_id is None:
        log.warning(
            "action_agent.no_review_transition", step="transition", issue_key=issue_key
        )
        return False
    await jira.issue_transitions.create(
        issue_id_or_key=issue_key, transition={"id": transition_id}
    )
    log.info("action_agent.transitioned", step="transition", issue_key=issue_key)
    return True


async def process_action_items() -> dict:
    """Run the full pipeline once. Never raises.

    Returns {"considered", "drafted", "repaired", "failed"} or {"skipped": reason}.
    """
    if os.environ.get("ACTION_AGENT_ENABLED", "false").lower() != "true":
        return {"skipped": "disabled"}
    if not os.environ.get("AIRBYTE_AGENTS_CLIENT_ID"):
        log.warning("action_agent.no_credentials", step="init")
        return {"skipped": "no_credentials"}

    considered = drafted = repaired = failed = 0
    try:
        async with _make_connector() as jira:
            tickets = await find_eligible_tickets(jira)
            considered = len(tickets)
            for ticket in tickets:
                key = ticket["key"]
                try:
                    if await has_agent_draft(jira, key):
                        # Comment landed last run but the transition failed.
                        await transition_to_review(jira, key)
                        repaired += 1
                        continue
                    context_text, nodes_count = await build_context(
                        ticket["summary"], ticket["description"]
                    )
                    draft = await draft_deliverable(
                        ticket["summary"], ticket["description"], context_text
                    )
                    if not draft:
                        failed += 1
                        continue
                    await post_draft(jira, key, draft, nodes_count)
                    await transition_to_review(jira, key)
                    drafted += 1
                except Exception as exc:
                    failed += 1
                    log.error(
                        "action_agent.ticket_failed",
                        step="pipeline", issue_key=key, error=str(exc),
                    )
    except Exception as exc:
        log.error("action_agent.run_failed", step="run", error=str(exc))
        return {"considered": considered, "drafted": drafted,
                "repaired": repaired, "failed": failed}

    summary = {"considered": considered, "drafted": drafted,
               "repaired": repaired, "failed": failed}
    log.info("action_agent.run_complete", step="run", **summary)
    return summary
```

- [ ] **Step 4: Run the full phase-27 file, then the whole suite**

```bash
docker compose cp transform_service/action_agent.py transform_service:/app/transform_service/
docker compose exec -w /app transform_service python -m pytest tests/test_phase27_action_agent.py -q
docker compose exec -w /app transform_service python -m pytest tests/ -q
```

Expected: phase-27 all PASS; full suite `101 + new` passed, 0 failed.

- [ ] **Step 5: Commit**

```bash
git add transform_service/action_agent.py tests/test_phase27_action_agent.py
git commit -m "feat: action agent comment posting, transition, and orchestrator"
```

---

### Task 5: Wiring — triggers, endpoint, Makefile, CLAUDE.md

**Files:**
- Modify: `transform_service/main.py`
- Modify: `Makefile`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `action_agent.process_action_items() -> dict` (Task 4).
- Produces: `POST /agent/actions/run` endpoint; scheduler job `action_agent_poll`; `make action-agent-run`.

- [ ] **Step 1: Wire main.py**

In `transform_service/main.py`:

1. Extend the existing transform_service import line:

```python
from transform_service import action_agent, db, episodic_memory, graph_algorithms, memgraph_client, procedural_memory, semantic_memory, vector_memory
```

2. In `lifespan`, after the existing scheduler jobs:

```python
    scheduler.add_job(
        action_agent.process_action_items,
        "interval", minutes=5, id="action_agent_poll",
    )
```

3. In `webhook_airbyte`, after the existing `background_tasks.add_task(...)` lines:

```python
    background_tasks.add_task(action_agent.process_action_items)
```

4. New endpoint, after the `/graph/memory/sessions` handler:

```python
@app.post("/agent/actions/run")
async def agent_actions_run() -> Dict[str, Any]:
    return await action_agent.process_action_items()
```

- [ ] **Step 2: Add Makefile target**

Next to the existing `trigger:` target:

```makefile
action-agent-run:
	curl -s -X POST http://localhost:8000/agent/actions/run | python3 -m json.tool
```

- [ ] **Step 3: Amend CLAUDE.md**

a. In "Graph Memory + Advanced Algorithms", append:

```markdown
- `action_agent.py` is the ONLY place the Airbyte Agent SDK (`airbyte-agent-sdk`)
  appears. It is a sanctioned query-time consumer of `memory_retrieval` and must
  never be called from `graph_builder.py`.
```

b. In "Absolute Rules — Do NOT Violate", after the Jira REST rule, add:

```markdown
- DO NOT use the Airbyte Agent SDK outside `action_agent.py`
- DO NOT call `action_agent` functions from `graph_builder.py` (query-time only)
- DO NOT let the action agent set any Jira status other than In Review
```

c. In the Environment Variables block, after the Airbyte webhook line, add:

```env
# Action Agent (Airbyte Agents SDK — app.airbyte.ai; separate product from
# the ELT API credentials above)
ACTION_AGENT_ENABLED=true
ACTION_AGENT_BATCH_SIZE=5
AIRBYTE_AGENTS_CLIENT_ID=
AIRBYTE_AGENTS_CLIENT_SECRET=
AIRBYTE_AGENTS_CONNECTOR_ID=
```

- [ ] **Step 4: Deploy, verify no-credentials no-op, run full suite**

```bash
docker compose cp transform_service/main.py transform_service:/app/transform_service/
docker compose restart transform_service && sleep 5
curl -s -X POST http://localhost:8000/agent/actions/run | python3 -m json.tool
```

Expected (credentials not yet configured): `{"skipped": "no_credentials"}` — proves wiring works and fails safe.

```bash
docker compose exec -w /app transform_service python -m pytest tests/ -q
```

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add transform_service/main.py Makefile CLAUDE.md
git commit -m "feat: wire action agent into scheduler, webhook, and manual endpoint"
```

---

### Task 6: Live end-to-end (gated on user's manual app.airbyte.ai setup)

**Files:** none (verification only)

**Interfaces:**
- Consumes: user-completed setup — Jira connector added in app.airbyte.ai (org Onix), SDK credentials + connector id typed into `.env` by the user as `AIRBYTE_AGENTS_CLIENT_ID`, `AIRBYTE_AGENTS_CLIENT_SECRET`, `AIRBYTE_AGENTS_CONNECTOR_ID`.

- [ ] **Step 1: Confirm prerequisites (do not proceed without them)**

Ask the user to complete, in app.airbyte.ai (org Onix):
1. Add Connector → Jira → Token auth (Atlassian email + Jira API token)
2. Generate SDK client credentials; note the connector id
3. Type all three values into `.env` directly (never through chat)

Then verify presence without printing values:

```bash
grep -cE "^AIRBYTE_AGENTS_(CLIENT_ID|CLIENT_SECRET|CONNECTOR_ID)=..+" .env
```

Expected: `3`

- [ ] **Step 2: Restart service and run the probe**

```bash
docker compose up -d transform_service && sleep 5
docker compose exec transform_service python scripts/test_action_agent_sdk.py
```

Expected: `SDK connectivity OK.` + 3 sample issues. If auth fails, stop and debug credentials before continuing.

- [ ] **Step 3: Trigger the pipeline against the live board**

`SCRUM-47` ("Action requested (unspecified)") is To Do with the `meeting-action-item` label — the designated first candidate.

```bash
make action-agent-run
```

Expected: `{"considered": >=1, "drafted": >=1, "repaired": 0, "failed": 0}` (LM Studio drafting takes seconds-to-minutes per ticket on gemma3-12b).

- [ ] **Step 4: Verify all three surfaces**

1. **Jira:** SCRUM-47 has a comment starting with `[action-agent draft]` containing a real drafted deliverable, and status is In Review.
2. **Idempotency:** run `make action-agent-run` again → `{"considered": 0, ...}` (In Review tickets no longer match the JQL).
3. **Dashboard:** app.airbyte.ai → Sessions / Tool Calls shows the run's Jira operations (`issues.api_search`, `issue_comments.*`, `issue_transitions.*`) — the demo payoff. Ask the user to confirm visually.

- [ ] **Step 5: Commit any live-run fixes; update the demo guide if the flow changed**

```bash
docker compose logs transform_service --since=10m | grep action_agent
```

If live behavior forced code changes, fix with the TDD loop (failing test → fix → suite green) and commit:

```bash
git add -A && git commit -m "fix: action agent adjustments from live end-to-end run"
```
