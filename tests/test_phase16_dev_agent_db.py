"""Phase 16: Tests for dev_agent/db.py should_attempt logic."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub asyncpg and structlog for local test runs
for mod_name in ("asyncpg", "structlog"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        if mod_name == "structlog":
            stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

from dev_agent.models import DevAgentRun


# ---------------------------------------------------------------------------
# should_attempt logic — we test by mocking get_run
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_should_attempt_no_row():
    """No existing row → should attempt."""
    import dev_agent.db as db_mod
    with patch.object(db_mod, "get_run", AsyncMock(return_value=None)):
        assert await db_mod.should_attempt("SCRUM-1", max_attempts=1) is True


@pytest.mark.anyio
async def test_should_attempt_status_running():
    """Status=running → do not attempt again."""
    import dev_agent.db as db_mod
    run = DevAgentRun(ticket_key="SCRUM-1", status="running", attempt_count=1)
    with patch.object(db_mod, "get_run", AsyncMock(return_value=run)):
        assert await db_mod.should_attempt("SCRUM-1", max_attempts=3) is False


@pytest.mark.anyio
async def test_should_attempt_status_pr_opened():
    """Status=pr_opened → already done, do not attempt."""
    import dev_agent.db as db_mod
    run = DevAgentRun(ticket_key="SCRUM-2", status="pr_opened", attempt_count=1)
    with patch.object(db_mod, "get_run", AsyncMock(return_value=run)):
        assert await db_mod.should_attempt("SCRUM-2", max_attempts=3) is False


@pytest.mark.anyio
async def test_should_attempt_status_failed_under_limit():
    """Status=failed, attempt_count < max_attempts → can retry."""
    import dev_agent.db as db_mod
    run = DevAgentRun(ticket_key="SCRUM-3", status="failed", attempt_count=1)
    with patch.object(db_mod, "get_run", AsyncMock(return_value=run)):
        assert await db_mod.should_attempt("SCRUM-3", max_attempts=3) is True


@pytest.mark.anyio
async def test_should_attempt_status_failed_at_limit():
    """Status=failed, attempt_count == max_attempts → do not retry."""
    import dev_agent.db as db_mod
    run = DevAgentRun(ticket_key="SCRUM-4", status="failed", attempt_count=1)
    with patch.object(db_mod, "get_run", AsyncMock(return_value=run)):
        assert await db_mod.should_attempt("SCRUM-4", max_attempts=1) is False


@pytest.mark.anyio
async def test_should_attempt_status_failed_over_limit():
    """Status=failed, attempt_count > max_attempts → do not retry."""
    import dev_agent.db as db_mod
    run = DevAgentRun(ticket_key="SCRUM-5", status="failed", attempt_count=5)
    with patch.object(db_mod, "get_run", AsyncMock(return_value=run)):
        assert await db_mod.should_attempt("SCRUM-5", max_attempts=3) is False


# ---------------------------------------------------------------------------
# DevAgentRun model
# ---------------------------------------------------------------------------

class TestDevAgentRunModel:
    def test_defaults(self):
        run = DevAgentRun(ticket_key="SCRUM-1", status="queued")
        assert run.attempt_count == 0
        assert run.pr_url is None
        assert run.error is None

    def test_all_statuses_valid(self):
        for status in ("queued", "running", "pr_opened", "failed", "skipped"):
            run = DevAgentRun(ticket_key="X-1", status=status)
            assert run.status == status
