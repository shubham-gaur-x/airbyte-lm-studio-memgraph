"""Tests for Phase 25 — memory_retrieval.py."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import memory_retrieval


def _mock_lm(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


class _AsyncIter:
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


def _make_driver(rows_per_run=None):
    """rows_per_run: list of row-lists, one per session.run() call."""
    call_count = 0
    rpr = rows_per_run or []

    async def run_side_effect(cypher, **kwargs):
        nonlocal call_count
        idx = call_count
        call_count += 1
        rows = rpr[idx] if idx < len(rpr) else []
        result = AsyncMock()
        result.__aiter__ = MagicMock(return_value=_AsyncIter(rows))
        result.single = AsyncMock(return_value=rows[0] if rows else None)
        return result

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


# ---------------------------------------------------------------------------
# _extract_entities
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_extract_entities_valid_json():
    payload = json.dumps({"people": ["Alice"], "topics": ["roadmap"], "date_hint": "last week"})
    client = _mock_lm(payload)

    with (
        patch("transform_service.memory_retrieval._get_client", return_value=client),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        result = await memory_retrieval._extract_entities("What did Alice discuss about roadmap?")

    assert result["people"] == ["Alice"]
    assert result["topics"] == ["roadmap"]
    assert result["date_hint"] == "last week"


@pytest.mark.anyio
async def test_extract_entities_invalid_json_returns_empty_no_raise():
    client = _mock_lm("not json }{")

    with (
        patch("transform_service.memory_retrieval._get_client", return_value=client),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        result = await memory_retrieval._extract_entities("anything")

    assert result == {"people": [], "topics": [], "date_hint": None}


@pytest.mark.anyio
async def test_extract_entities_lm_error_returns_empty_no_raise():
    client = AsyncMock()
    client.chat.completions.create.side_effect = RuntimeError("LM down")

    with (
        patch("transform_service.memory_retrieval._get_client", return_value=client),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "test-model"}),
    ):
        result = await memory_retrieval._extract_entities("anything")

    assert result == {"people": [], "topics": [], "date_hint": None}


# ---------------------------------------------------------------------------
# full_memory_query
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_full_memory_query_calls_all_steps_and_returns_required_keys():
    mock_entities = {"people": [], "topics": [], "date_hint": None}
    mock_context = {"people": [], "topics": [], "algorithm_summary": ""}
    mock_node_ids = ["node-1"]
    mock_answer = "Alice discussed roadmap items."
    mock_session_id = "session-abc"

    with (
        patch("transform_service.memory_retrieval._extract_entities", return_value=mock_entities) as ext,
        patch("transform_service.memory_retrieval._assemble_context", return_value=(mock_context, mock_node_ids)) as asm,
        patch("transform_service.memory_retrieval._synthesize", return_value=mock_answer) as syn,
        patch("transform_service.memory_retrieval.episodic_memory.log_memory_session", return_value=mock_session_id) as log_sess,
    ):
        result = await memory_retrieval.full_memory_query("What did Alice discuss?")

    ext.assert_called_once()
    asm.assert_called_once()
    syn.assert_called_once()
    log_sess.assert_called_once_with("What did Alice discuss?", mock_answer, mock_node_ids)

    assert result["answer"] == mock_answer
    assert result["session_id"] == mock_session_id
    assert "nodes_used" in result
    assert "context_summary" in result
    assert result["context_summary"]["people_found"] == 0


# ---------------------------------------------------------------------------
# person_memory_profile
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_person_memory_profile_returns_empty_when_person_not_found():
    # All queries return empty
    driver = _make_driver(rows_per_run=[[] for _ in range(10)])

    with patch("transform_service.memory_retrieval.memgraph_client.get_driver", return_value=driver):
        result = await memory_retrieval.person_memory_profile("nobody@example.com")

    assert result == {}


@pytest.mark.anyio
async def test_person_memory_profile_returns_profile_when_found():
    person_row = {
        "id": "p-1", "name": "Alice", "email": "alice@example.com",
        "pagerank_score": 0.9, "community_id": 1,
        "betweenness_centrality": 0.5, "degree_centrality": 0.3, "wcc_id": 0,
    }
    chain_row = {"chain_depth": 3}

    # Queries in order: person, facts, preferences, knows, recent_meetings, chain_depth, procedures
    driver = _make_driver(rows_per_run=[
        [person_row],   # base person query
        [],             # facts
        [],             # preferences
        [],             # knows
        [],             # recent_meetings
        [chain_row],    # chain_depth (single())
        [],             # procedures
    ])

    with patch("transform_service.memory_retrieval.memgraph_client.get_driver", return_value=driver):
        result = await memory_retrieval.person_memory_profile("alice@example.com")

    assert result["name"] == "Alice"
    assert result["email"] == "alice@example.com"
    assert result["pagerank_score"] == 0.9
    assert "facts" in result
    assert "preferences" in result
    assert "knows" in result
    assert "recent_meetings" in result
    assert "procedures" in result
