"""Dev-agent run lifecycle: states, the legal-transition table, and deterministic IDs.

A run moves through an explicit state machine so a crashed process resumes from where
it left off instead of restarting or double-shipping. Illegal transitions raise, which
turns a logic bug into a loud failure instead of silent corruption.

    TRIAGED -> PLANNED -> IMPLEMENTING -> DEBUGGING -> REVIEWING -> SHIPPED -> CLOSED

with DEBUGGING -> IMPLEMENTING (self-fix) and REVIEWING -> IMPLEMENTING (review feedback)
as the two backward edges, and FAILED / NEEDS_HUMAN reachable from any active state.

ID derivation (single source of truth — re-derive identically everywhere):
  * run_id            = uuid5("dev-agent-run", f"{ticket_key}#{attempt}")
  * Ticket node       = uuid5("ticket", ticket_key)
  * PullRequest node  = uuid5("pullrequest", pr_url)
A writer/reader ID mismatch is a known past bug class, so these live in one place.
"""
from __future__ import annotations

from typing import Dict, Set

from transform_service.utils import uuid5_id

# --- States ---------------------------------------------------------------
TRIAGED = "TRIAGED"
PLANNED = "PLANNED"
IMPLEMENTING = "IMPLEMENTING"
DEBUGGING = "DEBUGGING"
REVIEWING = "REVIEWING"
SHIPPED = "SHIPPED"
CLOSED = "CLOSED"
FAILED = "FAILED"
NEEDS_HUMAN = "NEEDS_HUMAN"

ALL_STATES: Set[str] = {
    TRIAGED, PLANNED, IMPLEMENTING, DEBUGGING, REVIEWING, SHIPPED, CLOSED, FAILED, NEEDS_HUMAN,
}

# Terminal states: no run continues past these (FAILED may be retried as a NEW run/attempt).
TERMINAL_STATES: Set[str] = {CLOSED, FAILED, NEEDS_HUMAN}

# Any active (non-terminal) state may escalate to FAILED or NEEDS_HUMAN.
_ESCALATIONS: Set[str] = {FAILED, NEEDS_HUMAN}

# Legal forward/backward transitions, escalations added below.
_TRANSITIONS: Dict[str, Set[str]] = {
    TRIAGED: {PLANNED},
    PLANNED: {IMPLEMENTING},
    IMPLEMENTING: {DEBUGGING},
    DEBUGGING: {REVIEWING, IMPLEMENTING},   # self-fix loop
    REVIEWING: {SHIPPED, IMPLEMENTING},     # review-feedback loop
    SHIPPED: {CLOSED},
    CLOSED: set(),
    FAILED: set(),
    NEEDS_HUMAN: set(),
}
for _state, _targets in _TRANSITIONS.items():
    if _state not in TERMINAL_STATES:
        _targets |= _ESCALATIONS


class IllegalTransition(RuntimeError):
    """Raised when a run is asked to move between two states with no legal edge."""


def can_transition(from_state: str, to_state: str) -> bool:
    """True if ``from_state -> to_state`` is a legal edge."""
    return to_state in _TRANSITIONS.get(from_state, set())


def assert_transition(from_state: str, to_state: str) -> None:
    """Raise :class:`IllegalTransition` unless the edge is legal."""
    if from_state not in ALL_STATES:
        raise IllegalTransition(f"unknown source state {from_state!r}")
    if to_state not in ALL_STATES:
        raise IllegalTransition(f"unknown target state {to_state!r}")
    if not can_transition(from_state, to_state):
        raise IllegalTransition(f"illegal transition {from_state} -> {to_state}")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


# --- Deterministic IDs (single source of truth) ---------------------------
def run_id(ticket_key: str, attempt: int) -> str:
    return uuid5_id("dev-agent-run", f"{ticket_key}#{attempt}")


def ticket_node_id(ticket_key: str) -> str:
    return uuid5_id("ticket", ticket_key)


def pull_request_node_id(pr_url: str) -> str:
    return uuid5_id("pullrequest", pr_url)
