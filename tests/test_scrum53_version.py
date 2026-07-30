"""SCRUM-53: add a GET /version endpoint to transform_service that returns the current git
commit SHA and a timestamp"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest


def test_version_endpoint_registered_in_main():
    """Verify the endpoint is registered in the FastAPI app."""
    from transform_service import main
    paths = {r.path for r in main.app.routes}
    assert "/version" in paths


@pytest.mark.anyio
async def test_version_endpoint_returns_git_sha_and_timestamp():
    """Test the endpoint returns git commit SHA and a valid ISO timestamp."""
    from transform_service import main

    with patch.object(main, "_get_git_commit_sha", return_value="abc123def456"):
        with patch("transform_service.main.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
            out = await main.version()

    assert "git_commit_sha" in out
    assert "timestamp" in out
    assert out["git_commit_sha"] == "abc123def456"
    assert out["timestamp"] == "2026-07-30T12:00:00+00:00"


@pytest.mark.anyio
async def test_version_endpoint_handles_git_error():
    """Test the endpoint gracefully handles git errors."""
    from transform_service import main

    with patch.object(main, "_get_git_commit_sha", return_value="unknown"):
        with patch("transform_service.main.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
            out = await main.version()

    assert out["git_commit_sha"] == "unknown"
    assert "timestamp" in out


def test_get_git_commit_sha_success():
    """Test _get_git_commit_sha successfully retrieves the SHA."""
    from transform_service import main

    with patch.object(subprocess, "check_output", return_value="abc123def456\n"):
        sha = main._get_git_commit_sha()

    assert sha == "abc123def456"


def test_get_git_commit_sha_called_process_error():
    """Test _get_git_commit_sha handles CalledProcessError."""
    from transform_service import main

    with patch.object(
        subprocess, "check_output",
        side_effect=subprocess.CalledProcessError(1, "git")
    ):
        sha = main._get_git_commit_sha()

    assert sha == "unknown"


def test_get_git_commit_sha_file_not_found_error():
    """Test _get_git_commit_sha handles FileNotFoundError (git not found)."""
    from transform_service import main

    with patch.object(subprocess, "check_output", side_effect=FileNotFoundError()):
        sha = main._get_git_commit_sha()

    assert sha == "unknown"
