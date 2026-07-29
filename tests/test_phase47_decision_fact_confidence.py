"""Phase 47 (B2): extend P4 confidence gating to Decision (write-time) and Fact (read-time).

Decision gets the same per-item confidence ActionItem got in P4 (it was the other "dead
field" gap — a Decision had zero confidence tracking). Facts already have real confidence
dynamics (semantic_memory: start 0.3, +0.1 per repeat mention, capped 1.0) and
memory_retrieval already sorts by it — the gap there is a read-time floor so single-mention,
low-confidence facts don't pollute retrieval by default (mirrors JIRA_CONFIDENCE_THRESHOLD's
pattern, but at read time since Facts have no Jira-ticket-style write-time side effect).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service.models import Decision, ExtractedMeeting


# --- Decision model + backward-compat coercion -----------------------------
def test_decision_model_defaults_confidence_to_one():
    assert Decision(text="We will ship Friday").confidence == 1.0


def test_decision_explicit_confidence_preserved():
    assert Decision(text="Maybe", confidence=0.4).confidence == 0.4


def test_extracted_meeting_coerces_plain_string_decisions():
    """Backward-compat: existing callers/JSON with decisions=['text', ...] still work."""
    m = ExtractedMeeting(
        title="T", kind="meeting", platform="Zoom", date=date(2026, 7, 29), summary="s",
        decisions=["We will migrate to the new auth system"],
    )
    assert isinstance(m.decisions[0], Decision)
    assert m.decisions[0].text == "We will migrate to the new auth system"
    assert m.decisions[0].confidence == 1.0


def test_extracted_meeting_accepts_dict_decisions():
    m = ExtractedMeeting(
        title="T", kind="meeting", platform="Zoom", date=date(2026, 7, 29), summary="s",
        decisions=[{"text": "Budget approved", "confidence": 0.5}],
    )
    assert m.decisions[0].text == "Budget approved"
    assert m.decisions[0].confidence == 0.5


def test_extracted_meeting_empty_decisions_still_default():
    assert ExtractedMeeting(
        title="T", kind="meeting", platform="Zoom", date=date(2026, 7, 29), summary="s",
    ).decisions == []


# --- extractor prompt asks for confidence -----------------------------------
def test_extractor_system_prompt_mentions_decision_confidence():
    from transform_service import extractor
    assert "confidence" in extractor._SYSTEM_PROMPT.lower()
    # the decisions field spec itself must now ask for structured {text, confidence}
    assert '"decisions"' in extractor._SYSTEM_PROMPT
    assert "confidence" in extractor._SYSTEM_PROMPT.split('"decisions"', 1)[1][:200].lower()


# --- memgraph_client writes decision confidence -----------------------------
@pytest.mark.anyio
async def test_upsert_meeting_graph_writes_decision_confidence():
    from transform_service import memgraph_client

    tx = AsyncMock(); tx.run = AsyncMock(); tx.commit = AsyncMock()
    tx_cm = MagicMock(); tx_cm.__aenter__ = AsyncMock(return_value=tx); tx_cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock(); session.begin_transaction = AsyncMock(return_value=tx_cm)
    session_cm = MagicMock(); session_cm.__aenter__ = AsyncMock(return_value=session); session_cm.__aexit__ = AsyncMock(return_value=False)
    driver = MagicMock(); driver.session.return_value = session_cm

    meeting = ExtractedMeeting(
        title="T", kind="meeting", platform="Zoom", date=date(2026, 7, 29), summary="s",
        decisions=[{"text": "Keep the current architecture", "confidence": 0.35}],
    )
    with (
        patch("transform_service.memgraph_client.get_driver", return_value=driver),
        patch.object(memgraph_client, "get_known_people", AsyncMock(return_value=[])),
        patch.object(memgraph_client.person_resolver, "load_roster", return_value=memgraph_client.person_resolver.Roster([])),
    ):
        await memgraph_client.upsert_meeting_graph(meeting, "src-dec-1")

    params = [c.kwargs for c in tx.run.call_args_list]
    decision_writes = [p for p in params if p.get("text") == "Keep the current architecture"]
    assert decision_writes and decision_writes[0]["confidence"] == 0.35


# --- Fact read-time confidence floor ----------------------------------------
def _empty_result():
    r = AsyncMock()

    async def _aiter(self=None):
        return
        yield  # pragma: no cover - makes this an async generator

    r.__aiter__ = _aiter
    r.single = AsyncMock(return_value=None)  # episodic_chain_depth calls result.single()
    return r


def _make_session():
    session = AsyncMock()
    session.run = AsyncMock(return_value=_empty_result())
    return session


def _fact_call(session) -> "MagicMock | None":
    """Find the session.run call whose Cypher touches :Fact, across all calls made."""
    for c in session.run.call_args_list:
        if ":Fact)" in c.args[0] or "HAS_FACT" in c.args[0]:
            return c
    return None


def _rows_result(rows):
    r = AsyncMock()

    async def _aiter(self=None):
        for row in rows:
            yield row

    r.__aiter__ = _aiter
    r.single = AsyncMock(return_value=rows[0] if rows else None)
    return r


async def _dispatch_by_cypher(cypher, **kwargs):
    """Key the response off the Cypher text rather than call order — person_memory_profile
    issues many queries (base, facts, preferences, knows, meetings, chain depth,
    procedures, ...) and only the base-person and Facts ones matter to these tests."""
    if "RETURN p.id" in cypher and "HAS_FACT" not in cypher:
        return _rows_result([{"id": "p1", "name": "Alice", "email": "alice@example.com"}])
    return _empty_result()


@pytest.mark.anyio
async def test_person_memory_profile_filters_low_confidence_facts_by_default():
    from transform_service import memory_retrieval

    session = _make_session()
    session.run = AsyncMock(side_effect=_dispatch_by_cypher)
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(memory_retrieval.memgraph_client, "get_driver", return_value=driver),
        patch.dict("os.environ", {}, clear=False),
    ):
        await memory_retrieval.person_memory_profile("alice@example.com")

    call = _fact_call(session)
    assert call is not None
    assert call.kwargs.get("min_confidence") == pytest.approx(0.5)  # FACT_MIN_CONFIDENCE default
    assert "confidence" in call.args[0] and ">=" in call.args[0]


@pytest.mark.anyio
async def test_person_memory_profile_respects_fact_min_confidence_env():
    from transform_service import memory_retrieval

    session = _make_session()
    session.run = AsyncMock(side_effect=_dispatch_by_cypher)
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(memory_retrieval.memgraph_client, "get_driver", return_value=driver),
        patch.dict("os.environ", {"FACT_MIN_CONFIDENCE": "0.8"}),
    ):
        await memory_retrieval.person_memory_profile("alice@example.com")

    call = _fact_call(session)
    assert call is not None
    assert call.kwargs.get("min_confidence") == pytest.approx(0.8)
