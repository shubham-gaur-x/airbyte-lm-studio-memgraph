"""Phase 35 (P8): self-verification of a dev-agent PR diff against the ticket intent."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from dev_agent import self_verify
from dev_agent.self_verify import VerifyVerdict

TICKET = {"key": "SCRUM-50", "summary": "Fix the read-back bug", "description": "..."}


@pytest.mark.anyio
async def test_verify_pr_pass(monkeypatch):
    monkeypatch.delenv("DEV_AGENT_VERIFY_THRESHOLD", raising=False)
    with patch.object(self_verify.claude_runner, "run_oneshot",
                      AsyncMock(return_value='{"addresses": true, "confidence": 0.9, "reason": "matches"}')):
        v = await self_verify.verify_pr(TICKET, "diff --git a/x b/x\n+fix")
    assert v.checked and v.addresses and v.confidence == 0.9
    assert v.passed is True


@pytest.mark.anyio
async def test_verify_pr_fail_low_confidence(monkeypatch):
    monkeypatch.delenv("DEV_AGENT_VERIFY_THRESHOLD", raising=False)
    with patch.object(self_verify.claude_runner, "run_oneshot",
                      AsyncMock(return_value='{"addresses": true, "confidence": 0.3, "reason": "stub only"}')):
        v = await self_verify.verify_pr(TICKET, "diff")
    assert v.checked and v.passed is False  # below 0.6 threshold


@pytest.mark.anyio
async def test_verify_pr_fail_does_not_address():
    with patch.object(self_verify.claude_runner, "run_oneshot",
                      AsyncMock(return_value='{"addresses": false, "confidence": 0.95, "reason": "unrelated"}')):
        v = await self_verify.verify_pr(TICKET, "diff")
    assert v.checked and v.addresses is False and v.passed is False


@pytest.mark.anyio
async def test_verify_pr_tolerates_prose_wrapped_json():
    """The scorer may wrap the object in prose or a fence — grab the first {...}."""
    with patch.object(self_verify.claude_runner, "run_oneshot",
                      AsyncMock(return_value='Here is my verdict:\n```json\n{"addresses": true, "confidence": 0.8, "reason": "ok"}\n```')):
        v = await self_verify.verify_pr(TICKET, "diff")
    assert v.checked and v.confidence == 0.8


@pytest.mark.anyio
async def test_verify_pr_unparseable_is_not_checked():
    with patch.object(self_verify.claude_runner, "run_oneshot",
                      AsyncMock(return_value="the model rambled with no json")):
        v = await self_verify.verify_pr(TICKET, "diff")
    assert v.checked is False and v.passed is False


@pytest.mark.anyio
async def test_verify_pr_empty_diff_short_circuits():
    with patch.object(self_verify.claude_runner, "run_oneshot", AsyncMock()) as mock_run:
        v = await self_verify.verify_pr(TICKET, "   ")
    assert v.checked is False
    mock_run.assert_not_awaited()  # never spends a scoring call on an empty diff


@pytest.mark.anyio
async def test_verify_pr_scorer_none_output_is_not_checked():
    with patch.object(self_verify.claude_runner, "run_oneshot", AsyncMock(return_value=None)):
        v = await self_verify.verify_pr(TICKET, "diff")
    assert v.checked is False


def test_passed_property_respects_threshold_env(monkeypatch):
    monkeypatch.setenv("DEV_AGENT_VERIFY_THRESHOLD", "0.85")
    assert VerifyVerdict(checked=True, addresses=True, confidence=0.8).passed is False
    assert VerifyVerdict(checked=True, addresses=True, confidence=0.9).passed is True
