"""GitHub webhook handler (P2): sync merge / push / commit data back into the graph.

Mirrors the ``/webhook/airbyte`` pattern in ``main.py``. This module only PARSES inbound
GitHub payloads and dispatches to ``memgraph_client`` — it issues no Cypher itself (that
stays in ``memgraph_client``) and makes no outbound GitHub REST calls (the push payload
already carries the commit + file lists). The join key back to a run is the ``agent/<KEY>``
branch that ``dev_agent`` produces.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, List, Optional

import structlog

from transform_service import memgraph_client

log = structlog.get_logger()

_BRANCH_PREFIX = "agent/"
_REF_PREFIX = "refs/heads/"


def ref_to_branch(ref: str) -> str:
    return ref[len(_REF_PREFIX):] if ref.startswith(_REF_PREFIX) else ref


def ticket_key_from_ref(ref_or_branch: str) -> Optional[str]:
    """`refs/heads/agent/SCRUM-50` or `agent/SCRUM-50` -> `SCRUM-50`; None for other branches."""
    branch = ref_to_branch(ref_or_branch or "")
    if branch.startswith(_BRANCH_PREFIX):
        key = branch[len(_BRANCH_PREFIX):].strip()
        return key or None
    return None


def verify_signature(raw: bytes, signature_header: Optional[str], secret: Optional[str]) -> bool:
    """HMAC-SHA256 check. Unset secret -> accept (dev default, mirrors the Airbyte webhook)."""
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature_header)


def _files_from_commit(c: Dict[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for path in c.get("added", []) or []:
        out.append({"path": path, "change_type": "added"})
    for path in c.get("modified", []) or []:
        out.append({"path": path, "change_type": "modified"})
    for path in c.get("removed", []) or []:
        out.append({"path": path, "change_type": "removed"})
    return out


async def handle_event(event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a GitHub webhook event to the appropriate graph write. Returns a status dict."""
    if event == "pull_request":
        pr = payload.get("pull_request") or {}
        action = payload.get("action")
        key = ticket_key_from_ref((pr.get("head") or {}).get("ref", ""))
        if action == "closed" and pr.get("merged") and key:
            await memgraph_client.merge_ticket_resolved_by_pr(
                key, pr.get("html_url", ""), merged_at=pr.get("merged_at"),
            )
            log.info("github_webhook.pr_merged", ticket=key, pr=pr.get("html_url"))
            return {"status": "resolved", "ticket": key}
        return {"status": "ignored", "reason": f"pull_request action={action} merged={pr.get('merged')}"}

    if event == "push":
        branch = ref_to_branch(payload.get("ref", ""))
        key = ticket_key_from_ref(branch)
        if not key:
            return {"status": "ignored", "reason": "non-agent branch"}
        commits = [
            {"sha": c.get("id", ""), "message": c.get("message", ""), "files": _files_from_commit(c)}
            for c in (payload.get("commits") or []) if c.get("id")
        ]
        res = await memgraph_client.write_commits_and_files(branch, commits)
        log.info("github_webhook.push", ticket=key, **res)
        return {"status": "ok", "ticket": key, **res}

    if event == "check_suite":
        # Not tracked as graph nodes yet — acknowledge so GitHub stops retrying.
        return {"status": "ignored", "reason": "check_suite not tracked yet"}

    return {"status": "ignored", "reason": f"event={event}"}
