"""Tests for Phase 21 — graph_algorithms.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from transform_service import graph_algorithms


def _make_mock_session(run_side_effects=None):
    """Build a mock neo4j AsyncSession that records run() calls."""
    session = AsyncMock()
    if run_side_effects:
        session.run.side_effect = run_side_effects
    else:
        session.run.return_value = AsyncMock()
    return session


def _make_driver(session):
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


# ---------------------------------------------------------------------------
# run_fast_algorithms
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_run_fast_algorithms_calls_all_five():
    session = _make_mock_session()
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        result = await graph_algorithms.run_fast_algorithms()

    assert result["algorithms_run"] == [
        "pagerank", "community_detection", "betweenness_centrality", "degree_centrality", "wcc"
    ]
    assert session.run.call_count == 5

    # Verify each CALL keyword appears once in the cypher calls
    cypher_calls = [str(c.args[0]) for c in session.run.call_args_list]
    assert any("pagerank.get()" in c for c in cypher_calls)
    assert any("community_detection.get()" in c for c in cypher_calls)
    assert any("betweenness_centrality.get()" in c for c in cypher_calls)
    assert any("degree_centrality.get()" in c for c in cypher_calls)
    assert any("weakly_connected_components.get()" in c for c in cypher_calls)


@pytest.mark.anyio
async def test_run_fast_algorithms_one_failure_does_not_abort_others():
    """If pagerank fails, the other four algorithms must still run."""
    call_count = 0

    async def run_side_effect(cypher, **kwargs):
        nonlocal call_count
        call_count += 1
        if "pagerank" in cypher and "pagerank_score" in cypher:
            raise RuntimeError("pagerank unavailable")
        return AsyncMock()

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        result = await graph_algorithms.run_fast_algorithms()

    assert session.run.call_count == 5
    assert result["results"]["pagerank"].startswith("failed:")
    for algo in ["community_detection", "betweenness_centrality", "degree_centrality", "wcc"]:
        assert result["results"][algo] == "ok"


# ---------------------------------------------------------------------------
# run_full_algorithms
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_run_full_algorithms_uses_leiden():
    session = _make_mock_session()
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        result = await graph_algorithms.run_full_algorithms()

    assert "leiden_community_detection" in result["algorithms_run"]
    assert "community_detection" not in result["algorithms_run"]

    cypher_calls = [str(c.args[0]) for c in session.run.call_args_list]
    assert any("igraphalg.community_leiden" in c for c in cypher_calls)
    # Regular community_detection.get() must NOT appear in the full run
    assert not any("community_detection.get()" in c for c in cypher_calls)


@pytest.mark.anyio
async def test_run_full_algorithms_individual_failure_continues():
    async def run_side_effect(cypher, **kwargs):
        if "betweenness_centrality" in cypher:
            raise RuntimeError("bc failed")
        return AsyncMock()

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        result = await graph_algorithms.run_full_algorithms()

    assert result["results"]["betweenness_centrality"].startswith("failed:")
    assert result["results"]["pagerank"] == "ok"
    assert result["results"]["leiden_community_detection"] == "ok"


# ---------------------------------------------------------------------------
# get_jaccard_similarity
# ---------------------------------------------------------------------------

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


@pytest.mark.anyio
async def test_get_jaccard_similarity_returns_max_score():
    # jaccard_pairwise can yield multiple rows for one pair; take the max
    rows = [{"similarity": 0.4}, {"similarity": 0.75}]
    result_obj = AsyncMock()
    result_obj.__aiter__ = MagicMock(return_value=_AsyncIter(rows))

    session = AsyncMock()
    session.run.return_value = result_obj
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        score = await graph_algorithms.get_jaccard_similarity("id-a", "id-b")

    assert score == 0.75
    cypher = session.run.call_args.args[0]
    assert "node_similarity.jaccard_pairwise" in cypher


@pytest.mark.anyio
async def test_get_jaccard_similarity_returns_zero_when_no_record():
    result_obj = AsyncMock()
    result_obj.__aiter__ = MagicMock(return_value=_AsyncIter([]))

    session = AsyncMock()
    session.run.return_value = result_obj
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        score = await graph_algorithms.get_jaccard_similarity("id-a", "id-b")

    assert score == 0.0


# ---------------------------------------------------------------------------
# vector_search
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_vector_search_returns_hits():
    rows = [
        {"node_id": "m1", "similarity": 0.9, "distance": 0.1},
        {"node_id": "m2", "similarity": 0.7, "distance": 0.3},
    ]
    result_obj = AsyncMock()
    result_obj.__aiter__ = MagicMock(return_value=_AsyncIter(rows))

    session = AsyncMock()
    session.run.return_value = result_obj
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        hits = await graph_algorithms.vector_search("meeting_embedding_idx", [0.1, 0.2, 0.3], limit=5)

    assert hits == rows
    cypher = session.run.call_args.args[0]
    assert "vector_search.search" in cypher
    kwargs = session.run.call_args.kwargs
    assert kwargs["index_name"] == "meeting_embedding_idx"
    assert kwargs["limit"] == 5
    assert kwargs["query_vector"] == [0.1, 0.2, 0.3]


@pytest.mark.anyio
async def test_vector_search_no_hits_returns_empty_list():
    result_obj = AsyncMock()
    result_obj.__aiter__ = MagicMock(return_value=_AsyncIter([]))

    session = AsyncMock()
    session.run.return_value = result_obj
    driver = _make_driver(session)

    with patch("transform_service.graph_algorithms.memgraph_client.get_driver", return_value=driver):
        hits = await graph_algorithms.vector_search("fact_embedding_idx", [0.5], limit=3)

    assert hits == []
