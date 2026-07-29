"""Phase 43 (P6): meeting-type routing + type-specific extraction prompt."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service import extractor, meeting_type_router as mtr


# --- route: grounded in the real meeting titles from the graph ------------
def test_route_email_source_is_always_email_thread():
    assert mtr.route("anything", source_type="email") == "email_thread"


def test_route_standup_family():
    assert mtr.route("Weekly Touchpoints - QA AI Adoption Pilot") == "standup"
    assert mtr.route("K8s Benchmarking Sync") == "standup"
    assert mtr.route("QA AI Pilot : Touchpoints") == "standup"
    assert mtr.route("Talend to dbt Tool Updates") == "standup"


def test_route_planning_and_review():
    assert mtr.route("QA AI Pilot - KPI Discussion") == "planning"
    assert mtr.route("QA AI Project: UI Improvement workshop") == "planning"
    assert mtr.route("CBS - Demo") == "review"
    assert mtr.route("401k Education Session w/Empower") == "review"


def test_route_general_fallback():
    assert mtr.route("Coffee") == "general"


def test_every_type_has_defined_prompt_hint():
    for t in mtr.TYPES:
        assert isinstance(mtr.prompt_hint(t), str)
    # non-empty for the specific types, empty for general
    assert mtr.prompt_hint("standup")
    assert mtr.prompt_hint("general") == ""


# --- extractor injects the type hint into the system prompt ---------------
@pytest.mark.anyio
async def test_extract_meeting_appends_type_hint_to_system_prompt():
    captured = {}

    async def _create(**kwargs):
        captured["messages"] = kwargs["messages"]
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = '{"title": "t", "kind": "standup", "platform": "Zoom", "date": "2026-07-28", "summary": "s"}'
        return resp

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)

    with (
        patch.object(extractor, "_get_client", return_value=client),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "gemma", "LM_STUDIO_BASE_URL": "http://x/v1"}),
    ):
        await extractor.extract_meeting("body", "calendar_event", type_hint=mtr.prompt_hint("standup"))

    system = captured["messages"][0]["content"]
    assert "Meeting-type guidance:" in system
    assert "standup" in system.lower()


@pytest.mark.anyio
async def test_extract_meeting_without_hint_is_unchanged():
    captured = {}

    async def _create(**kwargs):
        captured["messages"] = kwargs["messages"]
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = '{"title": "t", "kind": "meeting", "platform": "Zoom", "date": "2026-07-28", "summary": "s"}'
        return resp

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=_create)

    with (
        patch.object(extractor, "_get_client", return_value=client),
        patch.dict("os.environ", {"LM_STUDIO_MODEL": "gemma", "LM_STUDIO_BASE_URL": "http://x/v1"}),
    ):
        await extractor.extract_meeting("body", "email")

    assert "Meeting-type guidance:" not in captured["messages"][0]["content"]
