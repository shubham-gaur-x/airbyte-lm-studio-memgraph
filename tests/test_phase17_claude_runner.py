"""Phase 17: Tests for dev_agent/claude_runner.py."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub structlog
if "structlog" not in sys.modules:
    stub = types.ModuleType("structlog")
    stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
    sys.modules["structlog"] = stub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(returncode: int, stdout: bytes, stderr: bytes):
    """Build a mock asyncio subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _json_stdout(result: str = "done", num_turns: int = 3, is_error: bool = False) -> bytes:
    return json.dumps({
        "result": result,
        "num_turns": num_turns,
        "is_error": is_error,
    }).encode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_successful_json_result():
    """Happy path: proc exits 0, valid JSON, is_error=False."""
    from dev_agent.claude_runner import run_claude_code

    proc = _make_proc(0, _json_stdout("Implemented the feature."), b"")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_claude_code("/work/SCRUM-1", "implement ticket", 600, 40)

    assert result.success is True
    assert result.returncode == 0
    assert "Implemented" in result.result_text
    assert result.num_turns == 3
    assert result.timed_out is False


@pytest.mark.anyio
async def test_nonzero_exit():
    """Non-zero returncode → success=False, result_text from stderr."""
    from dev_agent.claude_runner import run_claude_code

    proc = _make_proc(1, b"", b"fatal error: something went wrong")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_claude_code("/work/SCRUM-2", "implement ticket", 600, 40)

    assert result.success is False
    assert result.returncode == 1
    assert "fatal error" in result.result_text


@pytest.mark.anyio
async def test_timeout():
    """asyncio.wait_for raises TimeoutError → timed_out=True, returncode=-1."""
    from dev_agent.claude_runner import run_claude_code

    proc = _make_proc(0, b"", b"")

    async def _raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await run_claude_code("/work/SCRUM-3", "implement ticket", 1, 40)

    assert result.success is False
    assert result.timed_out is True
    assert result.returncode == -1


@pytest.mark.anyio
async def test_malformed_json_stdout():
    """stdout is not valid JSON despite returncode==0 → fallback, success=True."""
    from dev_agent.claude_runner import run_claude_code

    proc = _make_proc(0, b"not-json-at-all!!!", b"")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_claude_code("/work/SCRUM-4", "implement ticket", 600, 40)

    assert result.success is True  # fallback, not crash
    assert result.returncode == 0


@pytest.mark.anyio
async def test_is_error_true_in_json():
    """When JSON has is_error=True, success should be False."""
    from dev_agent.claude_runner import run_claude_code

    proc = _make_proc(0, _json_stdout("failed", is_error=True), b"")

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
        result = await run_claude_code("/work/SCRUM-5", "implement ticket", 600, 40)

    assert result.success is False
    assert result.returncode == 0


@pytest.mark.anyio
async def test_env_anthropic_api_key_cleared():
    """ANTHROPIC_API_KEY must be empty so no real Anthropic calls happen."""
    import os
    from dev_agent.claude_runner import run_claude_code

    captured_env = {}

    async def _fake_exec(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        proc = _make_proc(0, _json_stdout(), b"")
        return proc

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-real-key"}):
        with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            await run_claude_code("/work/SCRUM-6", "test", 600, 40)

    assert captured_env.get("ANTHROPIC_API_KEY") == ""
