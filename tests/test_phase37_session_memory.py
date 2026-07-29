"""Phase 37 (P7): resumable dev-agent session memory (Matteo's AgentMemory shape)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from dev_agent import session_memory

TICKET = {"key": "SCRUM-50", "summary": "Fix the Jira read-back bug in sync_jira_issue"}
PR = {"number": 1, "html_url": "https://github.com/o/r/pull/1"}


# --- files_from_diff -------------------------------------------------------
def test_files_from_diff_parses_paths():
    diff = (
        "diff --git a/transform_service/jira_agent.py b/transform_service/jira_agent.py\n"
        "index 9fa76c0..f268212 100644\n--- a/transform_service/jira_agent.py\n"
        "diff --git a/transform_service/memgraph_client.py b/transform_service/memgraph_client.py\n"
    )
    assert session_memory.files_from_diff(diff) == [
        "transform_service/jira_agent.py",
        "transform_service/memgraph_client.py",
    ]


def test_files_from_diff_empty():
    assert session_memory.files_from_diff("") == []


# --- build_memory: the eight AgentMemory sections --------------------------
def test_build_memory_pr_opened_has_resume_and_work():
    mem = session_memory.build_memory(
        TICKET, outcome="pr_opened", pr=PR,
        files_changed=["a.py", "b.py"], raw_notes="full transcript",
    )
    assert mem["outcome"] == "pr_opened"
    assert mem["files_changed"] == ["a.py", "b.py"]
    assert any("pull" in w.lower() or "pr" in w.lower() for w in mem["work_completed"])
    assert PR["html_url"] in mem["resume_context"]
    assert mem["raw_notes"] == "full transcript"  # NOT truncated
    assert mem["confidence_keywords"]  # retrieval keywords present
    # all eight canonical sections exist
    for k in ("quick_reference", "decisions", "work_completed", "files_changed",
              "blockers", "next_actions", "resume_context", "raw_notes"):
        assert k in mem


def test_build_memory_failed_records_blocker_and_resume():
    mem = session_memory.build_memory(
        TICKET, outcome="failed", error="error_max_turns: reached 40 turns",
    )
    assert mem["outcome"] == "failed"
    assert any("max_turns" in b for b in mem["blockers"])
    assert "failed" in mem["resume_context"]
    assert mem["next_actions"]  # tells the next attempt what to do


def test_build_memory_flags_unverified_pr():
    class V:
        checked = True
        passed = False
    mem = session_memory.build_memory(TICKET, outcome="pr_opened", pr=PR, verdict=V())
    assert any("did not confirm" in a.lower() for a in mem["next_actions"])


# --- record / load round-trip ---------------------------------------------
@pytest.mark.anyio
async def test_record_persists_and_returns():
    with patch.object(session_memory.db, "save_session_memory", AsyncMock()) as mock_save:
        mem = await session_memory.record(TICKET, outcome="failed", error="boom")
    mock_save.assert_awaited_once()
    assert mock_save.await_args.args[0] == "SCRUM-50"
    assert mock_save.await_args.args[1]["outcome"] == "failed"
    assert mem["outcome"] == "failed"


@pytest.mark.anyio
async def test_record_save_failure_is_swallowed():
    with patch.object(session_memory.db, "save_session_memory", AsyncMock(side_effect=RuntimeError("db down"))):
        mem = await session_memory.record(TICKET, outcome="failed", error="boom")
    assert mem["outcome"] == "failed"  # returns memory despite persistence error


@pytest.mark.anyio
async def test_load_resume_context_reads_prior_memory():
    with patch.object(session_memory.db, "get_session_memory",
                      AsyncMock(return_value={"resume_context": "pick up from X"})):
        rc = await session_memory.load_resume_context("SCRUM-50")
    assert rc == "pick up from X"


@pytest.mark.anyio
async def test_load_resume_context_none_when_no_memory():
    with patch.object(session_memory.db, "get_session_memory", AsyncMock(return_value=None)):
        assert await session_memory.load_resume_context("SCRUM-50") is None


def test_build_prompt_injects_resume_context():
    from dev_agent import orchestrator
    p = orchestrator.build_prompt(
        {"key": "SCRUM-50", "summary": "x", "description": "y"},
        resume_context="Previous attempt failed: do Z next.",
    )
    assert "Resume context" in p and "do Z next" in p
    # and omitted when absent
    p2 = orchestrator.build_prompt({"key": "SCRUM-50", "summary": "x", "description": "y"})
    assert "Resume context" not in p2
