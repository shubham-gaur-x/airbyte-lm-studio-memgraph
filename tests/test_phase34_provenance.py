"""Phase 34 (P2): provenance chain — AgentRun bridge node + aligned edges.

Vocabulary is aligned with Matteo's engagement ontology (base.yaml):
  Meeting --results_in--> DevLog --implements--> Feature ; DevLog --follows_up_on--> Meeting
Our substrate maps DevLog -> AgentRun, Feature -> Ticket, and stitches the meeting-side
graph (ActionItem) to the dev-agent-side graph (Ticket) via TICKETED_AS, so one MATCH
returns meeting -> action item -> ticket -> agent run -> PR.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import memgraph_client
from transform_service.utils import uuid5_id


def _make_tx_driver():
    """Mock driver for: async with driver.session() as s: async with await s.begin_transaction() as tx."""
    tx = AsyncMock()
    tx.run = AsyncMock()
    tx.commit = AsyncMock()
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=tx)
    tx_cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.begin_transaction = AsyncMock(return_value=tx_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock()
    driver.session.return_value = session_cm
    return driver, tx


def _all_cypher(tx) -> str:
    return "\n".join(call.args[0] for call in tx.run.call_args_list)


def _all_params(tx) -> dict:
    merged: dict = {}
    for call in tx.run.call_args_list:
        merged.update(call.kwargs)
    return merged


@pytest.mark.anyio
async def test_write_run_provenance_merges_nodes_and_aligned_edges():
    driver, tx = _make_tx_driver()
    with patch("transform_service.memgraph_client.get_driver", return_value=driver):
        await memgraph_client.write_run_provenance(
            ticket_key="SCRUM-50",
            attempt=1,
            pr_url="https://github.com/o/r/pull/1",
            pr_number=1,
            branch="agent/SCRUM-50",
            ticket_summary="Fix the Jira read-back bug",
        )

    cypher = _all_cypher(tx)
    # Bridge node + endpoints
    assert "MERGE (run:AgentRun" in cypher
    assert "MERGE (pr:PullRequest" in cypher
    assert "MERGE (t:Ticket" in cypher
    # Matteo-aligned edges
    assert "IMPLEMENTS" in cypher          # AgentRun -> Ticket   (his DevLog implements Feature)
    assert "PRODUCED" in cypher            # AgentRun -> PullRequest
    assert "TICKETED_AS" in cypher         # ActionItem -> Ticket (stitches the two halves)
    assert "FOLLOWS_UP_ON" in cypher       # AgentRun -> Meeting  (his DevLog follows_up_on Meeting)
    # All writes MERGE, never CREATE (CLAUDE.md rule)
    assert "CREATE (" not in cypher


@pytest.mark.anyio
async def test_write_run_provenance_ids_match_lifecycle_derivation():
    """Writer must derive the same node ids dev_agent/lifecycle.py uses, or reads never match."""
    driver, tx = _make_tx_driver()
    with patch("transform_service.memgraph_client.get_driver", return_value=driver):
        await memgraph_client.write_run_provenance(
            ticket_key="SCRUM-50", attempt=2,
            pr_url="https://github.com/o/r/pull/7",
        )

    params = _all_params(tx)
    assert params["run_id"] == uuid5_id("dev-agent-run", "SCRUM-50#2")
    assert params["ticket_id"] == uuid5_id("ticket", "SCRUM-50")
    assert params["pr_id"] == uuid5_id("pullrequest", "https://github.com/o/r/pull/7")


@pytest.mark.anyio
async def test_write_run_provenance_is_single_transaction():
    driver, tx = _make_tx_driver()
    with patch("transform_service.memgraph_client.get_driver", return_value=driver):
        await memgraph_client.write_run_provenance(
            ticket_key="SCRUM-99", attempt=1, pr_url="https://github.com/o/r/pull/9",
        )
    tx.commit.assert_awaited_once()
    assert tx.run.await_count >= 1


@pytest.mark.anyio
async def test_write_run_provenance_returns_node_ids():
    driver, tx = _make_tx_driver()
    with patch("transform_service.memgraph_client.get_driver", return_value=driver):
        out = await memgraph_client.write_run_provenance(
            ticket_key="SCRUM-50", attempt=1, pr_url="https://github.com/o/r/pull/1",
        )
    assert out["run_id"] == uuid5_id("dev-agent-run", "SCRUM-50#1")
    assert out["ticket_id"] == uuid5_id("ticket", "SCRUM-50")
    assert out["pr_id"] == uuid5_id("pullrequest", "https://github.com/o/r/pull/1")
