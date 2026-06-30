"""Phase 14: Tests for is_engineering_task classification."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transform_service.models import ActionItem, ExtractedMeeting

# Provide a minimal stub for openai so the extractor module can be imported
# without having the real package installed (it lives only in Docker).
if "openai" not in sys.modules:
    _openai_stub = types.ModuleType("openai")
    _openai_stub.AsyncOpenAI = MagicMock  # type: ignore[attr-defined]
    _openai_stub.APIConnectionError = Exception  # type: ignore[attr-defined]
    sys.modules["openai"] = _openai_stub


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestActionItemModel:
    def test_default_is_false(self):
        item = ActionItem(owner="Alice", task="Do something")
        assert item.is_engineering_task is False

    def test_explicit_true(self):
        item = ActionItem(owner="Bob", task="Fix the bug", is_engineering_task=True)
        assert item.is_engineering_task is True

    def test_explicit_false(self):
        item = ActionItem(owner="Carol", task="Schedule a call", is_engineering_task=False)
        assert item.is_engineering_task is False

    def test_parse_from_dict_missing_field_defaults_false(self):
        data = {"owner": "Dave", "task": "Write docs"}
        item = ActionItem.model_validate(data)
        assert item.is_engineering_task is False

    def test_parse_from_dict_with_field(self):
        data = {"owner": "Eve", "task": "Add endpoint", "is_engineering_task": True}
        item = ActionItem.model_validate(data)
        assert item.is_engineering_task is True

    def test_extracted_meeting_action_items_default(self):
        """ExtractedMeeting round-trips with is_engineering_task absent → False."""
        payload = {
            "title": "Sprint Planning",
            "kind": "meeting",
            "platform": "Zoom",
            "date": "2026-06-29",
            "summary": "Planned the sprint.",
            "action_items": [
                {"owner": "Alice", "task": "Ship feature X"}
            ],
        }
        meeting = ExtractedMeeting.model_validate(payload)
        assert meeting.action_items[0].is_engineering_task is False

    def test_extracted_meeting_action_items_true(self):
        payload = {
            "title": "Sprint Planning",
            "kind": "meeting",
            "platform": "Zoom",
            "date": "2026-06-29",
            "summary": "Planned the sprint.",
            "action_items": [
                {"owner": "Alice", "task": "Ship feature X", "is_engineering_task": True}
            ],
        }
        meeting = ExtractedMeeting.model_validate(payload)
        assert meeting.action_items[0].is_engineering_task is True


# ---------------------------------------------------------------------------
# Extractor sanitization tests
# ---------------------------------------------------------------------------

def _make_llm_response(action_items: list) -> str:
    """Build a minimal valid LLM JSON response."""
    return json.dumps({
        "title": "Test Meeting",
        "kind": "meeting",
        "platform": "Zoom",
        "date": "2026-06-29",
        "start_time": None,
        "end_time": None,
        "duration_minutes": None,
        "location": None,
        "attendees": [{"name": "Alice", "email": "alice@example.com", "role": "host"}],
        "summary": "A test meeting.",
        "topics": ["testing"],
        "decisions": [],
        "action_items": action_items,
        "key_quotes": [],
        "links": [],
        "sentiment": "neutral",
        "follow_up_needed": False,
        "confidence": 0.9,
    })


@pytest.fixture
def mock_openai_response():
    """Return a factory for mocking openai chat completion responses."""
    def factory(content: str):
        choice = MagicMock()
        choice.message.content = content
        response = MagicMock()
        response.choices = [choice]
        return response
    return factory


@pytest.mark.anyio
async def test_extractor_defaults_is_engineering_task_false_when_absent(mock_openai_response):
    """When LLM omits is_engineering_task, sanitization defaults it to False."""
    raw_content = _make_llm_response([
        {"owner": "Alice", "task": "Schedule meeting"}
        # no is_engineering_task key
    ])

    with patch.dict("os.environ", {
        "LM_STUDIO_BASE_URL": "http://localhost:1234/v1",
        "LM_STUDIO_MODEL": "gemma3-12b",
    }):
        import transform_service.extractor as extractor_mod
        extractor_mod._client = None  # reset singleton

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_openai_response(raw_content)
        )

        with patch.object(extractor_mod, "_get_client", return_value=mock_client):
            result = await extractor_mod.extract_meeting("test text", "email")

    assert result is not None
    assert result.action_items[0].is_engineering_task is False


@pytest.mark.anyio
async def test_extractor_preserves_is_engineering_task_true(mock_openai_response):
    """When LLM provides is_engineering_task=true, it is preserved."""
    raw_content = _make_llm_response([
        {"owner": "Bob", "task": "Fix the API bug", "is_engineering_task": True}
    ])

    with patch.dict("os.environ", {
        "LM_STUDIO_BASE_URL": "http://localhost:1234/v1",
        "LM_STUDIO_MODEL": "gemma3-12b",
    }):
        import transform_service.extractor as extractor_mod
        extractor_mod._client = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_openai_response(raw_content)
        )

        with patch.object(extractor_mod, "_get_client", return_value=mock_client):
            result = await extractor_mod.extract_meeting("test text", "email")

    assert result is not None
    assert result.action_items[0].is_engineering_task is True


@pytest.mark.anyio
async def test_extractor_defaults_false_not_true_when_missing(mock_openai_response):
    """Fail-safe: missing is_engineering_task → False, never True."""
    # Multiple items, none with the field
    raw_content = _make_llm_response([
        {"owner": "Alice", "task": "Schedule a call"},
        {"owner": "Bob", "task": "Write the report"},
    ])

    with patch.dict("os.environ", {
        "LM_STUDIO_BASE_URL": "http://localhost:1234/v1",
        "LM_STUDIO_MODEL": "gemma3-12b",
    }):
        import transform_service.extractor as extractor_mod
        extractor_mod._client = None

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_openai_response(raw_content)
        )

        with patch.object(extractor_mod, "_get_client", return_value=mock_client):
            result = await extractor_mod.extract_meeting("test text", "email")

    assert result is not None
    for item in result.action_items:
        assert item.is_engineering_task is False
