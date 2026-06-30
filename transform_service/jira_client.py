"""Jira REST API client — all Jira REST calls live here."""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
import structlog

from transform_service.utils import with_retry

log = structlog.get_logger()


def jira_headers() -> Dict[str, str]:
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    creds = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def jira_base_url() -> str:
    return f"https://{os.environ['JIRA_DOMAIN']}/rest/api/3"


def _adf_to_text(node: Any) -> str:
    """Recursively flatten an Atlassian Document Format node to plain text."""
    if not isinstance(node, dict):
        return ""
    node_type = node.get("type", "")
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    if node_type == "codeBlock":
        # Collect children text, wrap in newlines
        inner = "".join(_adf_to_text(c) for c in node.get("content", []))
        return f"\n{inner}\n"

    # All block/list types: recurse into content, add separator for blocks
    children = node.get("content") or []
    text = "".join(_adf_to_text(c) for c in children)

    if node_type in ("paragraph", "heading", "listItem"):
        return text + "\n"
    if node_type in ("bulletList", "orderedList"):
        return text
    return text


@with_retry(max_attempts=3, base_delay=2.0)
async def search_issues(
    jql: str,
    fields: Optional[List[str]] = None,
    max_results: int = 50,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"jql": jql, "maxResults": max_results}
    if fields:
        params["fields"] = ",".join(fields)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{jira_base_url()}/search/jql",
            params=params,
            headers=jira_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("issues", [])


async def list_eligible_tickets(
    project_key: str,
    statuses: List[str],
    skip_labels: List[str],
    require_description: bool = False,
) -> List[Dict[str, Any]]:
    status_list = ", ".join(f'"{s}"' for s in statuses)
    label_list = ", ".join(f'"{lbl}"' for lbl in skip_labels)
    jql = (
        f'project = "{project_key}" AND status in ({status_list})'
        f" AND labels not in ({label_list})"
    )
    if require_description:
        jql += " AND description is not EMPTY"
    jql += " ORDER BY created ASC"

    issues = await search_issues(
        jql,
        fields=["key", "summary", "status", "labels"],
    )
    return [
        {
            "key": i["key"],
            "summary": i["fields"].get("summary", ""),
            "status": (i["fields"].get("status") or {}).get("name", ""),
            "labels": i["fields"].get("labels") or [],
        }
        for i in issues
    ]


@with_retry(max_attempts=3, base_delay=2.0)
async def get_issue_detail(key: str) -> Dict[str, Any]:
    encoded_key = quote(key, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{jira_base_url()}/issue/{encoded_key}",
            params={"fields": "summary,description,status,labels,priority,assignee"},
            headers=jira_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        fields = data.get("fields", {})
        raw_desc = fields.get("description")
        description = _adf_to_text(raw_desc).strip() if raw_desc else ""
        return {
            "key": data["key"],
            "summary": fields.get("summary", ""),
            "description": description,
            "status": (fields.get("status") or {}).get("name", ""),
            "labels": fields.get("labels") or [],
            "priority": (fields.get("priority") or {}).get("name"),
        }


@with_retry(max_attempts=3, base_delay=2.0)
async def add_comment(key: str, text: str) -> None:
    encoded_key = quote(key, safe="")
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{jira_base_url()}/issue/{encoded_key}/comment",
            json=body,
            headers=jira_headers(),
        )
        resp.raise_for_status()


@with_retry(max_attempts=3, base_delay=2.0)
async def get_transitions(key: str) -> List[Dict[str, Any]]:
    encoded_key = quote(key, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{jira_base_url()}/issue/{encoded_key}/transitions",
            headers=jira_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("transitions", [])


async def transition_issue(key: str, target_status_name: str) -> bool:
    transitions = await get_transitions(key)
    target = target_status_name.lower()
    match = next(
        (t for t in transitions if (t.get("to") or {}).get("name", "").lower() == target),
        None,
    )
    if match is None:
        available = [t.get("to", {}).get("name", "?") for t in transitions]
        log.warning(
            "jira_client.transition_not_found",
            key=key,
            target=target_status_name,
            available=available,
        )
        return False

    encoded_key = quote(key, safe="")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{jira_base_url()}/issue/{encoded_key}/transitions",
            json={"transition": {"id": match["id"]}},
            headers=jira_headers(),
        )
        resp.raise_for_status()

    log.info("jira_client.transitioned", key=key, to=target_status_name)
    return True
