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
from pydantic import BaseModel

from airbyte_agent_sdk.connectors.jira import JiraConnector
from airbyte_agent_sdk.types import AirbyteAuthConfig

from transform_service import memory_retrieval
from transform_service.extractor import _get_client
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
        auth_config=AirbyteAuthConfig(  # type: ignore[call-arg]  # workspace_name/organization_id resolved from env by the SDK
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
    """Flatten Atlassian Document Format to plain text.

    ADF shows up two ways here: plain dicts (our own comment bodies, built in
    _draft_comment_body) and SDK Pydantic models (comment bodies read back
    from the API, e.g. IssueCommentBody). Normalize the latter via
    model_dump() so one recursive walk handles both.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, BaseModel):
        node = node.model_dump()
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
        # rec is a real SDK Issue model — attribute access, not dict .get().
        # description/labels are dynamic fields (IssueFields allows extra),
        # so getattr with a default is required even though summary/status
        # are declared model fields.
        fields = rec.fields
        tickets.append({
            "key": rec.key or "",
            "summary": (getattr(fields, "summary", "") or "") if fields else "",
            "description": _adf_to_text(getattr(fields, "description", None) if fields else None),
        })
    log.info("action_agent.eligible", step="find", count=len(tickets))
    return tickets


@with_retry(max_attempts=3, base_delay=2.0)
async def has_agent_draft(jira: JiraConnector, issue_key: str) -> bool:
    """True if a previous run already posted the marker comment."""
    resp = await jira.issue_comments.list(issue_id_or_key=issue_key)
    for comment in _records(resp):
        body = getattr(comment, "body", None)
        if ACTION_AGENT_MARKER in _adf_to_text(body):
            return True
    return False


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
        body=_draft_comment_body(draft, nodes_count),  # type: ignore[arg-type]  # SDK accepts a plain dict body
    )
    log.info("action_agent.draft_posted", step="comment", issue_key=issue_key)


@with_retry(max_attempts=3, base_delay=2.0)
async def transition_to_review(jira: JiraConnector, issue_key: str) -> bool:
    """Move the ticket to In Review. False if no such transition exists."""
    resp = await jira.issue_transitions.list(issue_id_or_key=issue_key)
    transition_id: Optional[str] = None
    for t in _records(resp):
        to = getattr(t, "to", None)
        target = getattr(to, "name", None) if to else getattr(t, "name", None)
        if target == _REVIEW_STATUS:
            transition_id = getattr(t, "id", None)
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
                        # A repeat failure here counts as failed, not repaired
                        # — the marker stays present, so the next poll retries
                        # only the transition, never a second comment.
                        if await transition_to_review(jira, key):
                            repaired += 1
                        else:
                            failed += 1
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
                    # Same rule: comment posted but no transition available yet
                    # is not a completed drafted ticket — count it failed so
                    # the next poll's marker guard retries the transition only.
                    if await transition_to_review(jira, key):
                        drafted += 1
                    else:
                        failed += 1
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
