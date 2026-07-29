from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx
import structlog

from transform_service import dedup, memgraph_client, vector_memory
from transform_service.jira_client import add_comment as _add_comment
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


async def _find_duplicate(
    action: ActionItem, action_id: str, meeting: ExtractedMeeting, source_id: str,
) -> Optional[Dict[str, Any]]:
    """P5: return an existing open ActionItem this one duplicates (same owner, high
    similarity), after linking MENTIONED_IN + commenting on its ticket. None otherwise."""
    candidates = await memgraph_client.get_open_actions_for_owner(action.owner, action_id)
    if not candidates:
        return None
    threshold = float(os.environ.get("JIRA_DEDUP_THRESHOLD", "0.9"))
    new_embedding = await vector_memory.embed_text(action.task)
    match = dedup.best_match(action.task, new_embedding, candidates, threshold)
    if not match:
        return None

    meeting_id = uuid5_id("meeting", source_id)
    await memgraph_client.link_action_mentioned_in(match["id"], meeting_id)
    if match.get("jira_key"):
        try:
            await _add_comment(
                match["jira_key"],
                f'Also raised in "{meeting.title}" (dedup similarity {match["score"]:.2f}).',
            )
        except Exception as exc:
            log.warning("jira_pusher.dedup_comment_failed", key=match["jira_key"], error=str(exc))
    log.info(
        "jira_pusher.deduped", task=action.task[:60],
        matched_key=match.get("jira_key"), score=round(match["score"], 3),
    )
    return match


async def push_action_items(
    action_items: List[ActionItem],
    meeting: ExtractedMeeting,
    source_id: str,
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

        threshold = float(os.environ.get("JIRA_CONFIDENCE_THRESHOLD", "0.6"))

        for i, action in enumerate(action_items):
            # Must match memgraph_client.upsert_meeting_graph's derivation exactly —
            # that function seeds ActionItem.id from the raw source_id, not the
            # Meeting node's id, or this MATCH silently matches zero nodes.
            action_id = uuid5_id("action", f"{source_id}:{i}:{action.task}")

            # P4: gate low-confidence items into review instead of creating a Jira issue.
            if action.confidence < threshold:
                await memgraph_client.mark_action_needs_review(action_id, action.confidence)
                log.info(
                    "jira_pusher.needs_review",
                    task=action.task[:60], confidence=round(action.confidence, 2),
                )
                continue

            # P5: skip creating a duplicate of an existing open item (recurring meetings).
            if os.environ.get("JIRA_DEDUP_ENABLED", "true").lower() == "true":
                if await _find_duplicate(action, action_id, meeting, source_id):
                    continue

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
        source_id=source_id,
        total=len(action_items),
        created=len(created_keys),
        keys=created_keys,
    )
    return created_keys
