"""Phase 36 (P2): GitHub webhook -> provenance graph sync."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from transform_service import github_webhook as gw


# --- branch/ref -> ticket key ---------------------------------------------
def test_ticket_key_from_ref_variants():
    assert gw.ticket_key_from_ref("refs/heads/agent/SCRUM-50") == "SCRUM-50"
    assert gw.ticket_key_from_ref("agent/SCRUM-50") == "SCRUM-50"
    assert gw.ticket_key_from_ref("refs/heads/main") is None
    assert gw.ticket_key_from_ref("feature/x") is None
    assert gw.ticket_key_from_ref("agent/") is None


# --- signature verification -----------------------------------------------
def test_verify_signature_unset_secret_accepts():
    assert gw.verify_signature(b"body", None, None) is True


def test_verify_signature_valid_and_invalid():
    import hashlib
    import hmac
    secret = "s3cret"
    raw = b'{"a":1}'
    good = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert gw.verify_signature(raw, good, secret) is True
    assert gw.verify_signature(raw, "sha256=deadbeef", secret) is False
    assert gw.verify_signature(raw, None, secret) is False


# --- pull_request merged --------------------------------------------------
@pytest.mark.anyio
async def test_pull_request_merged_calls_resolver():
    payload = {
        "action": "closed",
        "pull_request": {
            "merged": True, "merged_at": "2026-07-28T20:00:00Z",
            "html_url": "https://github.com/o/r/pull/1",
            "head": {"ref": "agent/SCRUM-50"},
        },
    }
    with patch.object(gw.memgraph_client, "merge_ticket_resolved_by_pr", AsyncMock()) as mock_merge:
        out = await gw.handle_event("pull_request", payload)
    assert out["status"] == "resolved" and out["ticket"] == "SCRUM-50"
    mock_merge.assert_awaited_once()
    assert mock_merge.await_args.args[0] == "SCRUM-50"


@pytest.mark.anyio
async def test_pull_request_opened_not_merged_is_ignored():
    payload = {"action": "opened", "pull_request": {"merged": False, "head": {"ref": "agent/SCRUM-50"}}}
    with patch.object(gw.memgraph_client, "merge_ticket_resolved_by_pr", AsyncMock()) as mock_merge:
        out = await gw.handle_event("pull_request", payload)
    assert out["status"] == "ignored"
    mock_merge.assert_not_awaited()


# --- push -----------------------------------------------------------------
@pytest.mark.anyio
async def test_push_writes_commits_and_files():
    payload = {
        "ref": "refs/heads/agent/SCRUM-50",
        "commits": [
            {"id": "abc123", "message": "fix", "added": ["a.py"], "modified": ["b.py"], "removed": []},
        ],
    }
    with patch.object(gw.memgraph_client, "write_commits_and_files",
                      AsyncMock(return_value={"commits": 1, "files": 2})) as mock_write:
        out = await gw.handle_event("push", payload)
    assert out["status"] == "ok" and out["ticket"] == "SCRUM-50"
    mock_write.assert_awaited_once()
    branch, commits = mock_write.await_args.args
    assert branch == "agent/SCRUM-50"
    assert commits[0]["sha"] == "abc123"
    paths = {f["path"]: f["change_type"] for f in commits[0]["files"]}
    assert paths == {"a.py": "added", "b.py": "modified"}


@pytest.mark.anyio
async def test_push_to_non_agent_branch_ignored():
    payload = {"ref": "refs/heads/main", "commits": [{"id": "x"}]}
    with patch.object(gw.memgraph_client, "write_commits_and_files", AsyncMock()) as mock_write:
        out = await gw.handle_event("push", payload)
    assert out["status"] == "ignored"
    mock_write.assert_not_awaited()


@pytest.mark.anyio
async def test_unknown_event_ignored():
    out = await gw.handle_event("check_suite", {})
    assert out["status"] == "ignored"


# --- the actual /webhook/github route, not just handle_event ---------------
# Regression: a live v5.1 Phase E run hit `log.info("...", event=event)` in the real
# route — `event` collides with structlog's own reserved kwarg (the log message text)
# and raises TypeError at call time. Every prior test called handle_event() directly and
# never exercised this route function's own logging, so nothing caught it. Drive the real
# ASGI app end-to-end (not the Python function directly) so a real structlog call fires,
# same as production traffic.
@pytest.mark.anyio
async def test_webhook_github_route_accepts_real_request_without_logging_error():
    import httpx

    from transform_service import main

    with patch.object(main.github_webhook, "handle_event", AsyncMock()):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/webhook/github",
                json={"ref": "refs/heads/agent/SCRUM-1", "commits": []},
                headers={"X-GitHub-Event": "push"},
            )
    assert resp.status_code == 200
    assert resp.json() == {"status": "queued", "event": "push"}
