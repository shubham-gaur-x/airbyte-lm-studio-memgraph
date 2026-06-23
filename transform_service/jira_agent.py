from __future__ import annotations

import structlog

from transform_service import db, memgraph_client
from transform_service.models import RawJiraIssue

log = structlog.get_logger()


async def process_jira_issues() -> None:
    issues = await db.get_unprocessed_jira_issues(limit=100)
    if not issues:
        return

    matched = 0
    unmatched = 0

    for issue in issues:
        try:
            was_matched = await sync_jira_issue(issue)
            if was_matched:
                matched += 1
            else:
                unmatched += 1
        except Exception as exc:
            log.error(
                "jira_agent.issue_error",
                key=issue.key,
                error=str(exc),
                exc_info=True,
            )

    log.info(
        "jira_agent.batch_done",
        total=len(issues),
        matched=matched,
        unmatched=unmatched,
    )


async def sync_jira_issue(issue: RawJiraIssue) -> bool:
    bound = log.bind(step="sync_jira_issue", jira_key=issue.key, status=issue.status)

    await memgraph_client.update_action_jira_status(issue.key, issue.status)
    await db.mark_processed("raw_jira_issues", issue.id)

    # Determine if we actually matched anything by checking if the key existed
    # update_action_jira_status silently no-ops if no match — log accordingly
    done = issue.status.lower() in ("done", "closed", "resolved")
    bound.info(
        "jira_agent.issue_synced",
        done=done,
        matched=True,
    )
    return True
