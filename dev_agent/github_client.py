"""GitHub API client — read-only PR verification."""
from __future__ import annotations

import os
from typing import Optional

import httpx
import structlog

from transform_service.utils import with_retry

log = structlog.get_logger()


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@with_retry(max_attempts=3, base_delay=2.0)
async def find_open_pr(owner: str, repo: str, branch: str) -> Optional[dict]:
    """Return {number, html_url} for the first open PR on this branch, or None."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open"},
            headers=_github_headers(),
        )
        resp.raise_for_status()
        pulls = resp.json()

    if not pulls:
        return None
    pr = pulls[0]
    return {"number": pr["number"], "html_url": pr["html_url"]}


@with_retry(max_attempts=3, base_delay=2.0)
async def get_pr_diff(owner: str, repo: str, pr_number: int, max_bytes: int = 200_000) -> str:
    """Return the unified diff for a PR (truncated to ``max_bytes``), or "" if unavailable.

    Used by P8 self-verification to score the change against the ticket intent.
    """
    headers = _github_headers()
    headers["Accept"] = "application/vnd.github.v3.diff"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.text[:max_bytes]
