"""P7 session memory: a resumable record of each dev-agent run.

Modeled on Matteo's `AgentMemory` ontology (Quick Reference + confidence keywords,
Decisions, Work Completed, Files Changed, Blockers/Risks, Next Actions, Resume Context,
Raw Session Notes). Persisted in Postgres `dev_agent_runs.state_payload` (keyed by
ticket_key, survives across attempts) — NOT the graph AgentRun node — because the resume
read happens *before* a run, on a failed attempt where no PR (and no AgentRun node) exists
yet. A summary is mirrored onto the AgentRun node when a PR exists, for graph inspection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog

from dev_agent import db

log = structlog.get_logger()


def files_from_diff(diff: str) -> List[str]:
    """Extract changed file paths from a unified diff (the `b/` side of each `diff --git`)."""
    files: List[str] = []
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            if len(parts) == 2 and parts[1].strip():
                files.append(parts[1].strip())
    return files


def build_memory(
    ticket: Dict[str, Any],
    *,
    outcome: str,
    pr: Optional[Dict[str, Any]] = None,
    files_changed: Optional[List[str]] = None,
    error: Optional[str] = None,
    verdict: Any = None,
    raw_notes: str = "",
) -> Dict[str, Any]:
    """Assemble the AgentMemory record. ``outcome`` is 'pr_opened' or 'failed'."""
    key = ticket.get("key", "")
    summary = ticket.get("summary", "")
    files_changed = files_changed or []
    blockers: List[str] = []
    next_actions: List[str] = []

    if outcome == "pr_opened":
        work = [f"Opened PR {pr['html_url']}"] if pr else ["Opened a pull request"]
        if verdict is not None and getattr(verdict, "checked", False) and not verdict.passed:
            next_actions.append(
                "Automated check did not confirm the diff addresses the ticket — human review needed."
            )
        resume = (
            f"A PR is already open for {key}"
            + (f": {pr['html_url']}" if pr else "")
            + f". Files touched: {', '.join(files_changed) or 'unknown'}. "
            "If this ticket is reopened, refine that PR rather than starting from scratch."
        )
    else:  # failed
        work = []
        if error:
            blockers.append(error[:500])
        next_actions.append("Investigate the blocker below, then retry.")
        resume = (
            f"Previous attempt on {key} failed: {(error or 'unknown error')[:300]}. "
            f"Files touched so far: {', '.join(files_changed) or 'none recorded'}. "
            "Continue from there; do not redo work that already succeeded."
        )

    keywords = sorted({w.lower().strip('.,:;()') for w in summary.split() if len(w) > 3})[:8]
    return {
        "quick_reference": f"{key}: {outcome} — {summary}",
        "confidence_keywords": keywords,
        "decisions": [],
        "work_completed": work,
        "files_changed": files_changed,
        "blockers": blockers,
        "next_actions": next_actions,
        "resume_context": resume,
        "raw_notes": raw_notes or "",
        "outcome": outcome,
    }


async def record(
    ticket: Dict[str, Any],
    *,
    outcome: str,
    pr: Optional[Dict[str, Any]] = None,
    files_changed: Optional[List[str]] = None,
    error: Optional[str] = None,
    verdict: Any = None,
    raw_notes: str = "",
) -> Dict[str, Any]:
    """Build and persist the session memory (best-effort). Returns the memory dict."""
    memory = build_memory(
        ticket, outcome=outcome, pr=pr, files_changed=files_changed,
        error=error, verdict=verdict, raw_notes=raw_notes,
    )
    try:
        await db.save_session_memory(ticket.get("key", ""), memory)
    except Exception:
        log.warning("session_memory.save_failed", exc_info=True)
    return memory


async def load_resume_context(ticket_key: str) -> Optional[str]:
    """Return the prior attempt's resume_context for injection into a retry, or None."""
    try:
        memory = await db.get_session_memory(ticket_key)
    except Exception:
        return None
    if not memory:
        return None
    return memory.get("resume_context") or None
