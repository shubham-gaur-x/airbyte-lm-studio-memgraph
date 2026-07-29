"""P8 self-verification: score a dev-agent PR diff against the ticket intent.

Runs ONE cheap scoring pass through the same backend the coding run used
(``DEV_AGENT_LLM_BACKEND``). This scores *code*, so it is a sanctioned exception to the
"extraction is always local" rule (which governs meeting data only). Verification NEVER
blocks the In Review transition — a low score only flags the Jira comment and sets
``AgentRun.verified = false`` in the graph, so a human still reviews (per the P8 design).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import structlog
from pydantic import BaseModel, ConfigDict

from dev_agent import claude_runner

log = structlog.get_logger()


class VerifyVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checked: bool = False      # did a scoring pass actually run AND parse?
    addresses: bool = False    # does the diff plausibly satisfy the ticket?
    confidence: float = 0.0
    reason: str = ""

    @property
    def passed(self) -> bool:
        threshold = float(os.environ.get("DEV_AGENT_VERIFY_THRESHOLD", "0.6"))
        return self.checked and self.addresses and self.confidence >= threshold


_PROMPT = """You are reviewing whether a pull request diff actually implements a Jira ticket.

Ticket: {key}
Summary: {summary}
Description:
{description}

--- PR DIFF (unified) ---
{diff}
--- END DIFF ---

Decide whether the diff plausibly and substantially satisfies the ticket. Be strict: a
partial stub, an unrelated change, or a change that omits something the ticket explicitly
asked for is NOT a pass. Output ONLY one JSON object and nothing else:
{{"addresses": true|false, "confidence": 0.0-1.0, "reason": "<one sentence>"}}"""


def _truncate_diff(diff: str, max_chars: int = 24000) -> str:
    return diff if len(diff) <= max_chars else diff[:max_chars] + "\n... [diff truncated] ..."


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


async def verify_pr(
    ticket: Dict[str, Any],
    diff: str,
    *,
    timeout_seconds: Optional[int] = None,
    model: Optional[str] = None,
) -> VerifyVerdict:
    """Score ``diff`` against ``ticket``. Returns a verdict; on any failure ``checked=False``
    (couldn't verify) rather than raising — callers must not block the review transition on it.
    """
    if not diff or not diff.strip():
        return VerifyVerdict(checked=False, reason="empty diff")
    if timeout_seconds is None:
        timeout_seconds = int(os.environ.get("DEV_AGENT_VERIFY_TIMEOUT_SECONDS", "180"))

    prompt = _PROMPT.format(
        key=ticket.get("key", ""),
        summary=ticket.get("summary", ""),
        description=(ticket.get("description", "") or "")[:4000],
        diff=_truncate_diff(diff),
    )
    try:
        raw = await claude_runner.run_oneshot(prompt, timeout_seconds=timeout_seconds, model=model)
    except Exception as exc:
        log.warning("self_verify.run_failed", error=str(exc))
        return VerifyVerdict(checked=False, reason=f"scorer error: {exc}")

    if not raw:
        return VerifyVerdict(checked=False, reason="no scorer output")
    data = _parse_json(raw)
    if data is None:
        log.warning("self_verify.parse_failed", snippet=raw[:200])
        return VerifyVerdict(checked=False, reason="unparseable scorer output")

    verdict = VerifyVerdict(
        checked=True,
        addresses=bool(data.get("addresses", False)),
        confidence=float(data.get("confidence", 0.0) or 0.0),
        reason=str(data.get("reason", ""))[:500],
    )
    log.info(
        "self_verify.done",
        addresses=verdict.addresses, confidence=verdict.confidence, passed=verdict.passed,
    )
    return verdict
