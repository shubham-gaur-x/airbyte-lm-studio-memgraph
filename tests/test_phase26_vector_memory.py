"""Tests for vector_memory.py — embedding generation and semantic search."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import vector_memory


def _mock_embedding_client(vector=None, raises=False):
    client = AsyncMock()
    if raises:
        client.embeddings.create.side_effect = RuntimeError("LM Studio down")
    else:
        data_item = MagicMock()
        data_item.embedding = vector or [0.1, 0.2, 0.3]
        resp = MagicMock()
        resp.data = [data_item]
        client.embeddings.create = AsyncMock(return_value=resp)
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


def _make_driver(run_side_effect=None):
    session = AsyncMock()
    if run_side_effect:
        session.run.side_effect = run_side_effect
    else:
        session.run.return_value = AsyncMock()
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver, session


# ---------------------------------------------------------------------------
# embed_text
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_embed_text_returns_vector():
    client = _mock_embedding_client(vector=[0.1, 0.2, 0.3])
    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch.dict("os.environ", {"LM_STUDIO_EMBEDDING_MODEL": "test-embed-model"}),
    ):
        vector = await vector_memory.embed_text("some meeting summary")

    assert vector == [0.1, 0.2, 0.3]
    client.embeddings.create.assert_called_once()
    assert client.embeddings.create.call_args.kwargs["model"] == "test-embed-model"


@pytest.mark.anyio
async def test_embed_text_empty_string_returns_none_no_call():
    client = _mock_embedding_client()
    with patch("transform_service.vector_memory._get_client", return_value=client):
        vector = await vector_memory.embed_text("   ")

    assert vector is None
    client.embeddings.create.assert_not_called()


@pytest.mark.anyio
async def test_embed_text_lm_error_returns_none_no_raise():
    client = _mock_embedding_client(raises=True)
    with patch("transform_service.vector_memory._get_client", return_value=client):
        vector = await vector_memory.embed_text("some text")

    assert vector is None


# ---------------------------------------------------------------------------
# embed_meeting
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_embed_meeting_writes_embedding_property():
    client = _mock_embedding_client(vector=[0.1, 0.2])
    driver, session = _make_driver()

    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver),
    ):
        ok = await vector_memory.embed_meeting("meeting-1", "a real summary")

    assert ok is True
    cypher = session.run.call_args.args[0]
    assert "SET m.embedding" in cypher
    assert session.run.call_args.kwargs["embedding"] == [0.1, 0.2]


@pytest.mark.anyio
async def test_embed_meeting_embedding_failure_returns_false_no_write():
    client = _mock_embedding_client(raises=True)
    driver, session = _make_driver()

    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver),
    ):
        ok = await vector_memory.embed_meeting("meeting-1", "a real summary")

    assert ok is False
    session.run.assert_not_called()


# ---------------------------------------------------------------------------
# embed_facts_for_meeting
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_embed_facts_for_meeting_embeds_pending_facts():
    pending = [{"id": "fact-1", "text": "Alice leads backend"}, {"id": "fact-2", "text": "Q3 migration"}]

    call_count = 0

    async def run_side_effect(cypher, **kwargs):
        nonlocal call_count
        call_count += 1
        if "WHERE f.embedding IS NULL" in cypher:
            result = AsyncMock()
            result.__aiter__ = MagicMock(return_value=_AsyncIter(pending))
            return result
        return AsyncMock()

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    client = _mock_embedding_client(vector=[0.5, 0.6])

    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver),
    ):
        count = await vector_memory.embed_facts_for_meeting("meeting-1")

    assert count == 2
    assert client.embeddings.create.call_count == 2


@pytest.mark.anyio
async def test_embed_facts_for_meeting_no_pending_facts_returns_zero():
    async def run_side_effect(cypher, **kwargs):
        result = AsyncMock()
        result.__aiter__ = MagicMock(return_value=_AsyncIter([]))
        return result

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver):
        count = await vector_memory.embed_facts_for_meeting("meeting-1")

    assert count == 0


# ---------------------------------------------------------------------------
# search_similar_meetings / search_similar_facts
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_search_similar_meetings_returns_enriched_results():
    client = _mock_embedding_client(vector=[0.1, 0.2])
    hits = [{"node_id": "m1", "similarity": 0.9, "distance": 0.1}]
    meeting_rows = [{"id": "m1", "title": "Sprint Planning", "date": "2026-01-01", "summary": "...", "kind": "meeting"}]

    async def run_side_effect(cypher, **kwargs):
        result = AsyncMock()
        result.__aiter__ = MagicMock(return_value=_AsyncIter(meeting_rows))
        return result

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.graph_algorithms.vector_search", return_value=hits) as vs,
        patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver),
    ):
        results = await vector_memory.search_similar_meetings("what happened in sprint planning?", limit=5)

    vs.assert_called_once_with("meeting_embedding_idx", [0.1, 0.2], 5)
    assert len(results) == 1
    assert results[0]["title"] == "Sprint Planning"
    assert results[0]["similarity"] == 0.9


@pytest.mark.anyio
async def test_search_similar_meetings_embedding_failure_returns_empty():
    client = _mock_embedding_client(raises=True)
    with patch("transform_service.vector_memory._get_client", return_value=client):
        results = await vector_memory.search_similar_meetings("query", limit=5)

    assert results == []


@pytest.mark.anyio
async def test_search_similar_meetings_no_hits_returns_empty():
    client = _mock_embedding_client(vector=[0.1])
    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.graph_algorithms.vector_search", return_value=[]),
    ):
        results = await vector_memory.search_similar_meetings("query", limit=5)

    assert results == []


@pytest.mark.anyio
async def test_search_similar_facts_returns_enriched_results():
    client = _mock_embedding_client(vector=[0.3, 0.4])
    hits = [{"node_id": "f1", "similarity": 0.8, "distance": 0.2}]
    fact_rows = [{"id": "f1", "text": "Alice leads backend", "confidence": 0.5, "source_count": 2}]

    async def run_side_effect(cypher, **kwargs):
        result = AsyncMock()
        result.__aiter__ = MagicMock(return_value=_AsyncIter(fact_rows))
        return result

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.graph_algorithms.vector_search", return_value=hits),
        patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver),
    ):
        results = await vector_memory.search_similar_facts("who leads backend?", limit=5)

    assert len(results) == 1
    assert results[0]["text"] == "Alice leads backend"
    assert results[0]["similarity"] == 0.8


# ---------------------------------------------------------------------------
# backfill_embeddings
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_backfill_embeddings_processes_pending_nodes():
    pending_meetings = [{"id": "m1", "text": "summary one"}]
    pending_facts = [{"id": "f1", "text": "fact one"}]

    call_count = 0

    async def run_side_effect(cypher, **kwargs):
        nonlocal call_count
        call_count += 1
        result = AsyncMock()
        if "MATCH (m:Meeting)" in cypher and "summary" in cypher:
            result.__aiter__ = MagicMock(return_value=_AsyncIter(pending_meetings))
        elif "MATCH (f:Fact)" in cypher and "RETURN f.id" in cypher:
            result.__aiter__ = MagicMock(return_value=_AsyncIter(pending_facts))
        else:
            result.__aiter__ = MagicMock(return_value=_AsyncIter([]))
        return result

    session = AsyncMock()
    session.run.side_effect = run_side_effect
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    client = _mock_embedding_client(vector=[0.1, 0.1])

    with (
        patch("transform_service.vector_memory._get_client", return_value=client),
        patch("transform_service.vector_memory.memgraph_client.get_driver", return_value=driver),
    ):
        result = await vector_memory.backfill_embeddings()

    assert result == {"meetings_embedded": 1, "facts_embedded": 1}
