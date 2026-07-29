"""Phase 31: meeting quality scoring — component scorers, composite, insufficient-data."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# Stub structlog + the memgraph_client import so the pure module imports without a driver.
for mod_name in ("structlog",):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

from transform_service import meeting_quality as mq  # noqa: E402


class TestComponentScorers:
    def test_attendance_ratio(self):
        assert mq.score_attendance_ratio(8, 10) == 0.8
        assert mq.score_attendance_ratio(12, 10) == 1.0  # clamped
        assert mq.score_attendance_ratio(5, 0) is None    # no invited data
        assert mq.score_attendance_ratio(None, 10) is None

    def test_action_completion(self):
        assert mq.score_action_completion(2, 4) == 0.5
        assert mq.score_action_completion(0, 3) == 0.0
        assert mq.score_action_completion(0, 0) is None    # no action items -> insufficient
        assert mq.score_action_completion(1, None) is None

    def test_agenda_present(self):
        assert mq.score_agenda_present("Agenda:\n1. Budget\n2. Roadmap") == 1.0
        assert mq.score_agenda_present("quick sync, no structure") == 0.0
        assert mq.score_agenda_present("") is None
        assert mq.score_agenda_present(None) is None

    def test_yield_needs_duration(self):
        pop = [1.0, 2.0, 3.0, 4.0]
        assert mq.score_yield(2, None, pop) is None        # no duration -> insufficient
        assert mq.score_yield(2, 0, pop) is None
        # 2 decisions in 60 min = 2.0/hr -> percentile within [1,2,3,4]
        v = mq.score_yield(2, 60, pop)
        assert v is not None and 0.0 <= v <= 1.0

    def test_recurrence_health(self):
        assert mq.score_recurrence_health([0.8]) is None        # non-series
        assert mq.score_recurrence_health([]) is None
        declining = mq.score_recurrence_health([0.8, 0.6, 0.4])
        stable = mq.score_recurrence_health([0.6, 0.6, 0.6])
        assert declining is not None and stable is not None
        assert declining < stable                                # decay scores lower


class TestPercentileRank:
    def test_neutral_when_tiny_population(self):
        assert mq.percentile_rank(5.0, []) == 0.5
        assert mq.percentile_rank(5.0, [3.0]) == 0.5

    def test_ranks_within_population(self):
        pop = [1.0, 2.0, 3.0, 4.0]
        assert mq.percentile_rank(4.0, pop) == 1.0
        assert mq.percentile_rank(1.0, pop) == 0.25


class TestComposite:
    def test_weighted_mean_over_available(self):
        comps = {"attendance_ratio": 1.0, "action_completion": 0.0}
        # weights 0.15 and 0.25 renormalize -> (0.15*1 + 0.25*0)/0.40 = 0.375
        assert mq.composite_quality(comps) == 0.375

    def test_all_none_is_insufficient(self):
        comps = {"decision_yield": None, "action_yield": None, "agenda_present": None}
        assert mq.composite_quality(comps) is None

    def test_single_component_uses_its_own_value(self):
        assert mq.composite_quality({"agenda_present": 1.0}) == 1.0


class TestComputeQuality:
    def test_sparse_meeting_yields_insufficient(self):
        # No duration, no invited, no actions, no agenda -> everything None -> None composite.
        features = {"n_decisions": 0, "n_actions": 0, "attended": 3}
        out = mq.compute_quality(features, {"decision": [], "action": []})
        assert out["quality_score"] is None

    def test_rich_meeting_scores(self):
        features = {
            "attended": 9, "invited": 10,
            "n_decisions": 3, "n_actions": 5, "n_actions_done": 4,
            "duration_minutes": 60, "agenda_text": "Agenda: 1. plan",
            "recurrence_scores": [0.5, 0.6],
        }
        pop = {"decision": [1.0, 3.0, 5.0], "action": [2.0, 5.0, 8.0]}
        out = mq.compute_quality(features, pop)
        assert out["quality_score"] is not None
        assert out["components"]["attendance_ratio"] == 0.9
        assert out["components"]["action_completion"] == 0.8


class TestRanking:
    def test_top_and_bottom_ignores_insufficient(self):
        scored = [
            {"id": "a", "quality_score": 0.2},
            {"id": "b", "quality_score": 0.9},
            {"id": "c", "quality_score": None},   # excluded
            {"id": "d", "quality_score": 0.5},
        ]
        ranked = mq.top_and_bottom(scored, k=2)
        assert [m["id"] for m in ranked["lowest"]] == ["a", "d"]
        assert ranked["highest"][0]["id"] == "b"
        assert all(m["quality_score"] is not None for m in ranked["lowest"])
