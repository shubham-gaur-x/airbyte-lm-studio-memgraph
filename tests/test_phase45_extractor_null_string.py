"""Phase 45: regression for the literal-string "null" extraction bug.

Live A3 smoke test (v5.1 hardening) hit this on real gemma3-12b output: the model
emitted the JSON string "null" for `date` instead of a real null. `if not data.get(...)`
treats a non-empty string as truthy, so the fallback never applied and Pydantic
validation failed. Fixed via `_is_null_like`.
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "openai" not in sys.modules:
    _stub = types.ModuleType("openai")
    _stub.AsyncOpenAI = MagicMock  # type: ignore[attr-defined]
    _stub.APIConnectionError = Exception  # type: ignore[attr-defined]
    sys.modules["openai"] = _stub

from transform_service import extractor as extractor_mod


def _response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# --- _is_null_like ----------------------------------------------------------
def test_is_null_like_catches_none_and_placeholder_strings():
    for v in (None, "", "null", "Null", "NULL", " null ", "none", "n/a"):
        assert extractor_mod._is_null_like(v) is True
    for v in ("2026-07-28", "Zoom", 0, False, 0.0):
        assert extractor_mod._is_null_like(v) is False


# --- extract_meeting: literal-string "null" for date --------------------------
@pytest.mark.anyio
async def test_extract_meeting_recovers_from_literal_null_string_date():
    raw = json.dumps({
        "title": "Weekly Sync", "kind": "email_thread", "platform": "Email",
        "date": "null",  # the exact live failure mode
        "start_time": None, "end_time": None, "duration_minutes": None, "location": None,
        "attendees": [], "summary": "s", "topics": [], "decisions": [],
        "action_items": [{"owner": "null", "task": "null", "confidence": "null"}],
        "key_quotes": [], "links": [], "sentiment": "neutral",
        "follow_up_needed": False, "confidence": 0.9,
    })
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_response(raw))

    with (
        patch.dict("os.environ", {"LM_STUDIO_BASE_URL": "http://x/v1", "LM_STUDIO_MODEL": "gemma3-12b"}),
        patch.object(extractor_mod, "_get_client", return_value=client),
    ):
        result = await extractor_mod.extract_meeting("body", "email", context={"date": "2026-07-28"})

    assert result is not None
    assert str(result.date) == "2026-07-28"  # fell back to context date, not a parse error
    assert result.action_items[0].owner == "Unknown"
    assert result.action_items[0].task == "Follow-up required"
    assert result.action_items[0].confidence == 1.0


@pytest.mark.anyio
async def test_extract_meeting_null_string_date_falls_back_to_today_without_context():
    raw = json.dumps({
        "title": "T", "kind": "meeting", "platform": "null", "date": "null",
        "start_time": None, "end_time": None, "duration_minutes": None, "location": None,
        "attendees": [], "summary": "null", "topics": [], "decisions": [], "action_items": [],
        "key_quotes": [], "links": [], "sentiment": "neutral",
        "follow_up_needed": False, "confidence": 0.9,
    })
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=_response(raw))

    with (
        patch.dict("os.environ", {"LM_STUDIO_BASE_URL": "http://x/v1", "LM_STUDIO_MODEL": "gemma3-12b"}),
        patch.object(extractor_mod, "_get_client", return_value=client),
    ):
        result = await extractor_mod.extract_meeting("body", "meeting")

    assert result is not None                  # did not blow up on "null" date
    assert result.platform == "unknown"         # "null" platform also caught
    assert result.summary == "T"                # "null" summary falls back to title
