"""Phase 29: lifecycle state machine, deterministic IDs, and run-state coercion."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

for mod_name in ("structlog",):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

from dev_agent import lifecycle as lc  # noqa: E402
from dev_agent.models import DevAgentRun  # noqa: E402


class TestTransitionTable:
    @pytest.mark.parametrize(
        "src,dst",
        [
            (lc.TRIAGED, lc.PLANNED),
            (lc.PLANNED, lc.IMPLEMENTING),
            (lc.IMPLEMENTING, lc.DEBUGGING),
            (lc.DEBUGGING, lc.REVIEWING),
            (lc.DEBUGGING, lc.IMPLEMENTING),   # self-fix loop
            (lc.REVIEWING, lc.SHIPPED),
            (lc.REVIEWING, lc.IMPLEMENTING),   # review-feedback loop
            (lc.SHIPPED, lc.CLOSED),
        ],
    )
    def test_legal_edges(self, src, dst):
        assert lc.can_transition(src, dst)
        lc.assert_transition(src, dst)  # does not raise

    @pytest.mark.parametrize(
        "src,dst",
        [
            (lc.TRIAGED, lc.SHIPPED),        # skips the middle
            (lc.PLANNED, lc.CLOSED),
            (lc.CLOSED, lc.IMPLEMENTING),    # terminal has no exit
            (lc.SHIPPED, lc.REVIEWING),      # no backward edge here
        ],
    )
    def test_illegal_edges_raise(self, src, dst):
        assert not lc.can_transition(src, dst)
        with pytest.raises(lc.IllegalTransition):
            lc.assert_transition(src, dst)

    @pytest.mark.parametrize("state", [lc.TRIAGED, lc.PLANNED, lc.IMPLEMENTING, lc.DEBUGGING, lc.REVIEWING])
    def test_active_states_can_escalate(self, state):
        assert lc.can_transition(state, lc.FAILED)
        assert lc.can_transition(state, lc.NEEDS_HUMAN)

    @pytest.mark.parametrize("state", [lc.CLOSED, lc.FAILED, lc.NEEDS_HUMAN])
    def test_terminal_states_have_no_exit(self, state):
        assert lc.is_terminal(state)
        for other in lc.ALL_STATES:
            assert not lc.can_transition(state, other)

    def test_unknown_state_raises(self):
        with pytest.raises(lc.IllegalTransition):
            lc.assert_transition("BOGUS", lc.PLANNED)


class TestDeterministicIds:
    def test_run_id_stable_and_attempt_sensitive(self):
        assert lc.run_id("SCRUM-42", 1) == lc.run_id("SCRUM-42", 1)
        assert lc.run_id("SCRUM-42", 1) != lc.run_id("SCRUM-42", 2)

    def test_ticket_and_pr_ids_stable_and_distinct(self):
        t1 = lc.ticket_node_id("SCRUM-42")
        assert t1 == lc.ticket_node_id("SCRUM-42")
        pr = lc.pull_request_node_id("https://github.com/o/r/pull/7")
        assert pr == lc.pull_request_node_id("https://github.com/o/r/pull/7")
        assert t1 != pr


class TestRunStatePayloadCoercion:
    def test_jsonb_string_is_parsed(self):
        run = DevAgentRun(ticket_key="SCRUM-1", status="running", state="PLANNED",
                          state_payload='{"spec": "do X"}')
        assert run.state_payload == {"spec": "do X"}

    def test_none_becomes_empty_dict(self):
        run = DevAgentRun(ticket_key="SCRUM-1", status="running", state_payload=None)
        assert run.state_payload == {}
