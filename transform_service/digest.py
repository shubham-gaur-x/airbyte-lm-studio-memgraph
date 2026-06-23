from __future__ import annotations

from typing import Any, Dict, List

import structlog

from transform_service import memgraph_client

log = structlog.get_logger()


async def weekly_digest() -> Dict[str, Any]:
    timeline = await memgraph_client.get_timeline("week")

    meetings = timeline.get("meetings", [])
    decisions = timeline.get("decisions", [])
    action_items = timeline.get("action_items", [])

    open_actions = [a for a in action_items if not a.get("done")]
    closed_actions = [a for a in action_items if a.get("done")]

    high_priority = [a for a in open_actions if a.get("priority") == "high"]

    return {
        "period": "last_7_days",
        "summary": {
            "total_meetings": len(meetings),
            "total_decisions": len(decisions),
            "total_action_items": len(action_items),
            "open_action_items": len(open_actions),
            "closed_action_items": len(closed_actions),
            "high_priority_open": len(high_priority),
        },
        "meetings": meetings,
        "decisions": decisions,
        "action_items": {
            "open": open_actions,
            "closed": closed_actions,
            "high_priority": high_priority,
        },
    }
