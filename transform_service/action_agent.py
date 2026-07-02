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
