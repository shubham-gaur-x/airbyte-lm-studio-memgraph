"""Phase 48 (B3): review-queue surfacing — P3 PersonReview, P4 needs_review ActionItems,
P9 Blocker nodes are all written but nothing exposed them until now."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import memgraph_client


def _driver_with_rows(rows):
    result = AsyncMock()
    result.__aiter__.return_value = iter(rows)
    session = AsyncMock()
    session.run = AsyncMock(return_value=result)
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver, session


# --- memgraph_client readers -------------------------------------------------
@pytest.mark.anyio
async def test_get_actions_needing_review_queries_needs_review_status():
    rows = [{"id": "a1", "task": "shaky task", "owner": "bob", "confidence": 0.3, "jira_status": "needs_review"}]
    driver, session = _driver_with_rows(rows)
    with patch.object(memgraph_client, "get_driver", return_value=driver):
        out = await memgraph_client.get_actions_needing_review()
    assert out == rows
    cypher = session.run.call_args.args[0]
    assert "needs_review" in cypher and "ActionItem" in cypher


@pytest.mark.anyio
async def test_get_person_reviews_queries_pending_status():
    rows = [{"id": "r1", "name": "Ghost Attendee", "role": "attendee", "reason": "no-email-no-match", "meeting_title": "Sync"}]
    driver, session = _driver_with_rows(rows)
    with patch.object(memgraph_client, "get_driver", return_value=driver):
        out = await memgraph_client.get_person_reviews()
    assert out == rows
    cypher = session.run.call_args.args[0]
    assert "PersonReview" in cypher and "NEEDS_REVIEW" in cypher


@pytest.mark.anyio
async def test_get_open_blockers_queries_blocker_nodes():
    rows = [{"id": "b1", "description": "Workspace permission lockout", "ticket_key": "SCRUM-9", "raised_by": "dev-agent"}]
    driver, session = _driver_with_rows(rows)
    with patch.object(memgraph_client, "get_driver", return_value=driver):
        out = await memgraph_client.get_open_blockers()
    assert out == rows
    cypher = session.run.call_args.args[0]
    assert "Blocker" in cypher


# --- endpoints ---------------------------------------------------------------
def test_review_endpoints_registered_in_main():
    from transform_service import main
    paths = {r.path for r in main.app.routes}
    assert "/review/actions" in paths
    assert "/review/people" in paths
    assert "/review/blockers" in paths


@pytest.mark.anyio
async def test_review_actions_endpoint_shape():
    from transform_service import main
    with patch.object(main.memgraph_client, "get_actions_needing_review", AsyncMock(return_value=[{"id": "a1"}])):
        out = await main.review_actions()
    assert out == {"actions": [{"id": "a1"}], "count": 1}


@pytest.mark.anyio
async def test_review_people_endpoint_shape():
    from transform_service import main
    with patch.object(main.memgraph_client, "get_person_reviews", AsyncMock(return_value=[])):
        out = await main.review_people()
    assert out == {"people": [], "count": 0}


@pytest.mark.anyio
async def test_review_blockers_endpoint_shape():
    from transform_service import main
    with patch.object(main.memgraph_client, "get_open_blockers", AsyncMock(return_value=[{"id": "b1"}])):
        out = await main.review_blockers()
    assert out == {"blockers": [{"id": "b1"}], "count": 1}
