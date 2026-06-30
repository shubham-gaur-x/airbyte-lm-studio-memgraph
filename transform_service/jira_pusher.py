from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
import structlog

from transform_service import memgraph_client
from transform_service.jira_client import jira_base_url as _jira_base_url
from transform_service.jira_client import jira_headers as _jira_headers
from transform_service.models import ActionItem, ExtractedMeeting
from transform_service.utils import uuid5_id, with_retry

log = structlog.get_logger()

MEETING_ACTION_ITEM_LABEL = "meeting-action-item"


@with_retry(max_attempts=3, base_delay=2.0)
async def _get_active_sprint_id(client: httpx.AsyncClient) -> Optional[int]:
    board_id = os.environ.get("JIRA_BOARD_ID", "1")
    resp = await client.get(
        f"https://{os.environ['JIRA_DOMAIN']}/rest/agile/1.0/board/{board_id}/sprint",
        params={"state": "active"},
        headers=_jira_headers(),
    )
    resp.raise_for_status()
    sprints = resp.json().get("values", [])
    return sprints[0]["id"] if sprints else None


@with_retry(max_attempts=3, base_delay=2.0)
async def _create_jira_issue(
    client: httpx.AsyncClient,
    summary: str,
    description: str,
    priority: str,
    sprint_id: Optional[int],
    is_engineering_task: bool = False,
) -> str:
    project_key = os.environ["JIRA_PROJECT_KEY"]
    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")

    jira_priority = {"high": "High", "medium": "Medium", "low": "Low"}.get(priority, "Medium")

    fields: Dict[str, Any] = {
        "project": {"key": project_key},
        "summary": summary,
        "description": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        },
        "issuetype": {"name": issue_type},
        "priority": {"name": jira_priority},
    }
    if not is_engineering_task:
        fields["labels"] = [MEETING_ACTION_ITEM_LABEL]

    payload: Dict[str, Any] = {"fields": fields}

    resp = await client.post(
        f"{_jira_base_url()}/issue",
        json=payload,
        headers=_jira_headers(),
    )
    resp.raise_for_status()
    jira_key = resp.json()["key"]

    if sprint_id and priority == "high":
        try:
            await client.post(
                f"https://{os.environ['JIRA_DOMAIN']}/rest/agile/1.0/sprint/{sprint_id}/issue",
                json={"issues": [jira_key]},
                headers=_jira_headers(),
            )
        except Exception as exc:
            log.warning("jira_pusher.sprint_move_failed", key=jira_key, error=str(exc))

    return jira_key


async def push_action_items(
    action_items: List[ActionItem],
    meeting: ExtractedMeeting,
    meeting_node_id: str,
) -> List[str]:
    if not os.environ.get("JIRA_ENABLED", "false").lower() == "true":
        return []

    if not action_items:
        return []

    if not os.environ.get("JIRA_API_TOKEN"):
        log.warning("jira_pusher.no_token", hint="Set JIRA_API_TOKEN to enable Jira push")
        return []

    created_keys: List[str] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            sprint_id = await _get_active_sprint_id(client)
        except Exception as exc:
            log.warning("jira_pusher.sprint_fetch_failed", error=str(exc))
            sprint_id = None

        for i, action in enumerate(action_items):
            action_id = uuid5_id("action", f"{meeting_node_id}:{i}:{action.task}")
            description = (
                f"From meeting: {meeting.title} ({meeting.date})\n"
                f"Owner: {action.owner}\n"
                f"Due: {action.due or 'not specified'}"
            )
            try:
                jira_key = await _create_jira_issue(
                    client,
                    summary=action.task[:255],
                    description=description,
                    priority=action.priority,
                    sprint_id=sprint_id,
                    is_engineering_task=action.is_engineering_task,
                )
                await memgraph_client.update_action_jira_key(action_id, jira_key)
                created_keys.append(jira_key)
                log.info(
                    "jira_pusher.issue_created",
                    jira_key=jira_key,
                    task=action.task[:60],
                    priority=action.priority,
                )
            except Exception as exc:
                log.error(
                    "jira_pusher.issue_failed",
                    task=action.task[:60],
                    error=str(exc),
                )

    log.info(
        "jira_pusher.batch_done",
        meeting_id=meeting_node_id,
        total=len(action_items),
        created=len(created_keys),
        keys=created_keys,
    )
    return created_keys
