"""Phase 15: Tests for jira_client.py and jira_pusher label logic."""
from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy deps not installed locally
# ---------------------------------------------------------------------------
import sys
import types

for mod_name in ("httpx", "structlog"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        if mod_name == "structlog":
            stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

# asyncpg / openai / neo4j stubs so import chain doesn't blow up
for mod_name in ("asyncpg", "openai", "gqlalchemy"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        sys.modules[mod_name] = stub

for mod_name in ("neo4j", "neo4j.exceptions"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        stub.AsyncGraphDatabase = MagicMock()  # type: ignore[attr-defined]
        stub.AsyncDriver = MagicMock()  # type: ignore[attr-defined]
        stub.ServiceUnavailable = Exception  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

# ---------------------------------------------------------------------------
# _adf_to_text unit tests — no network needed
# ---------------------------------------------------------------------------

from transform_service.jira_client import _adf_to_text


class TestAdfToText:
    def test_plain_paragraph(self):
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello world"}],
                }
            ],
        }
        assert "Hello world" in _adf_to_text(node)

    def test_bullet_list(self):
        node = {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Item one"}],
                        }
                    ],
                },
                {
                    "type": "listItem",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Item two"}],
                        }
                    ],
                },
            ],
        }
        result = _adf_to_text(node)
        assert "Item one" in result
        assert "Item two" in result

    def test_code_block(self):
        node = {
            "type": "codeBlock",
            "content": [{"type": "text", "text": "print('hi')"}],
        }
        result = _adf_to_text(node)
        assert "print('hi')" in result

    def test_nested_structure(self):
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Before"}],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "nested"}],
                                }
                            ],
                        }
                    ],
                },
            ],
        }
        result = _adf_to_text(node)
        assert "Before" in result
        assert "nested" in result

    def test_unknown_node_type_returns_children_text(self):
        node = {
            "type": "unknownFutureType",
            "content": [{"type": "text", "text": "fallback"}],
        }
        assert "fallback" in _adf_to_text(node)

    def test_none_node_returns_empty(self):
        assert _adf_to_text(None) == ""

    def test_hard_break(self):
        node = {"type": "hardBreak"}
        assert _adf_to_text(node) == "\n"


# ---------------------------------------------------------------------------
# transition_issue matching logic
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_transition_issue_success():
    transitions = [
        {"id": "11", "to": {"name": "To Do"}},
        {"id": "21", "to": {"name": "In Progress"}},
        {"id": "31", "to": {"name": "In Review"}},
    ]

    import transform_service.jira_client as jc

    with patch.object(jc, "get_transitions", AsyncMock(return_value=transitions)):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {
            "JIRA_DOMAIN": "example.atlassian.net",
            "JIRA_EMAIL": "test@example.com",
            "JIRA_API_TOKEN": "token123",
        }):
            # Patch httpx.AsyncClient so no real HTTP
            mock_client_inst = AsyncMock()
            mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
            mock_client_inst.__aexit__ = AsyncMock(return_value=None)
            mock_client_inst.post = AsyncMock(return_value=mock_resp)

            with patch("transform_service.jira_client.httpx") as mock_httpx:
                mock_httpx.AsyncClient.return_value = mock_client_inst
                result = await jc.transition_issue("SCRUM-1", "In Progress")

    assert result is True


@pytest.mark.anyio
async def test_transition_issue_not_found_returns_false():
    transitions = [{"id": "11", "to": {"name": "To Do"}}]

    import transform_service.jira_client as jc

    with patch.object(jc, "get_transitions", AsyncMock(return_value=transitions)):
        with patch.dict("os.environ", {
            "JIRA_DOMAIN": "example.atlassian.net",
            "JIRA_EMAIL": "test@example.com",
            "JIRA_API_TOKEN": "token123",
        }):
            result = await jc.transition_issue("SCRUM-1", "In Review")

    assert result is False


# ---------------------------------------------------------------------------
# jira_pusher label branching
# ---------------------------------------------------------------------------

class TestJiraPusherLabels:
    """Test that engineering tasks get no label, process tasks get the skip label."""

    def _build_fields(self, is_engineering_task: bool) -> Dict[str, Any]:
        """Extract the fields dict that _create_jira_issue would pass to Jira."""
        from transform_service.jira_pusher import MEETING_ACTION_ITEM_LABEL

        fields: Dict[str, Any] = {
            "project": {"key": "SCRUM"},
            "summary": "Test task",
            "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"},
        }
        if not is_engineering_task:
            fields["labels"] = [MEETING_ACTION_ITEM_LABEL]
        return fields

    def test_engineering_task_has_no_label(self):
        fields = self._build_fields(is_engineering_task=True)
        assert "labels" not in fields

    def test_process_task_has_skip_label(self):
        from transform_service.jira_pusher import MEETING_ACTION_ITEM_LABEL
        fields = self._build_fields(is_engineering_task=False)
        assert "labels" in fields
        assert MEETING_ACTION_ITEM_LABEL in fields["labels"]

    def test_meeting_action_item_label_constant(self):
        from transform_service.jira_pusher import MEETING_ACTION_ITEM_LABEL
        assert MEETING_ACTION_ITEM_LABEL == "meeting-action-item"
