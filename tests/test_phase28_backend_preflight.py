"""Phase 28: backend resolution, LM Studio preflight, and sprint-membership triage JQL."""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps not installed locally (mirrors test_phase15 conventions).
# ---------------------------------------------------------------------------
for mod_name in ("httpx", "structlog"):
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        if mod_name == "structlog":
            stub.get_logger = lambda: MagicMock()  # type: ignore[attr-defined]
        sys.modules[mod_name] = stub

from dev_agent import backend  # noqa: E402
from transform_service.jira_client import build_sprint_jql  # noqa: E402


# ---------------------------------------------------------------------------
# resolve_backend_env — one branch per backend, key-safety invariants
# ---------------------------------------------------------------------------
class TestResolveBackendEnv:
    def test_local_empties_anthropic_key(self, monkeypatch):
        monkeypatch.setenv("LM_STUDIO_ANTHROPIC_URL", "http://host.docker.internal:1234")
        env = backend.resolve_backend_env("local")
        assert env["ANTHROPIC_API_KEY"] == ""  # api.anthropic.com unreachable
        assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:1234"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "lmstudio"

    def test_claude_passes_real_key_to_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        env = backend.resolve_backend_env("claude")
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-real"
        assert env["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"

    @pytest.mark.parametrize(
        "name,key_env",
        [("openrouter", "OPENROUTER_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY")],
    )
    def test_hosted_tiers_route_through_proxy(self, monkeypatch, name, key_env):
        monkeypatch.setenv(key_env, "provider-key")
        monkeypatch.setenv("LITELLM_PROXY_URL", "http://litellm:4000")
        env = backend.resolve_backend_env(name)
        assert env["ANTHROPIC_BASE_URL"] == "http://litellm:4000"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "provider-key"
        assert env["ANTHROPIC_API_KEY"] == ""


class TestGetBackend:
    def test_defaults_to_local(self, monkeypatch):
        monkeypatch.delenv("DEV_AGENT_LLM_BACKEND", raising=False)
        assert backend.get_backend() == "local"

    def test_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("DEV_AGENT_LLM_BACKEND", "openai")
        with pytest.raises(ValueError):
            backend.get_backend()


# ---------------------------------------------------------------------------
# select_loaded_model — pure check over /api/v0/models data
# ---------------------------------------------------------------------------
class TestSelectLoadedModel:
    def test_loaded_with_enough_context(self):
        models = [{"id": "qwen-coder", "state": "loaded", "loaded_context_length": 32768}]
        ok, detail = backend.select_loaded_model(models, 32768)
        assert ok is True
        assert "qwen-coder" in detail

    def test_no_model_loaded(self):
        models = [{"id": "gemma", "state": "not-loaded", "max_context_length": 131072}]
        ok, detail = backend.select_loaded_model(models, 32768)
        assert ok is False
        assert "no chat model" in detail

    def test_loaded_but_context_too_small(self):
        models = [{"id": "gemma", "state": "loaded", "loaded_context_length": 8192}]
        ok, detail = backend.select_loaded_model(models, 32768)
        assert ok is False
        assert "8192" in detail and "32768" in detail

    def test_only_embeddings_loaded_is_not_ready(self):
        models = [{"id": "nomic", "state": "loaded", "type": "embeddings", "loaded_context_length": 2048}]
        ok, _ = backend.select_loaded_model(models, 32768)
        assert ok is False


# ---------------------------------------------------------------------------
# preflight — async paths (mock the models fetch)
# ---------------------------------------------------------------------------
class TestPreflight:
    @pytest.mark.anyio
    async def test_local_ok(self, monkeypatch):
        monkeypatch.setenv("DEV_AGENT_MIN_CONTEXT", "32768")
        models = [{"id": "qwen", "state": "loaded", "loaded_context_length": 40000}]
        with patch("dev_agent.backend._fetch_models", new=AsyncMock(return_value=models)):
            detail = await backend.preflight("local")
        assert "qwen" in detail

    @pytest.mark.anyio
    async def test_local_no_model_raises(self, monkeypatch):
        monkeypatch.setenv("DEV_AGENT_MIN_CONTEXT", "32768")
        models = [{"id": "gemma", "state": "not-loaded"}]
        with patch("dev_agent.backend._fetch_models", new=AsyncMock(return_value=models)):
            with pytest.raises(backend.PreflightError):
                await backend.preflight("local")

    @pytest.mark.anyio
    async def test_local_unreachable_raises(self):
        with patch("dev_agent.backend._fetch_models", new=AsyncMock(side_effect=OSError("conn refused"))):
            with pytest.raises(backend.PreflightError):
                await backend.preflight("local")

    @pytest.mark.anyio
    async def test_claude_requires_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(backend.PreflightError):
            await backend.preflight("claude")

    @pytest.mark.anyio
    async def test_groq_requires_key(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(backend.PreflightError):
            await backend.preflight("groq")


# ---------------------------------------------------------------------------
# build_sprint_jql — sprint-membership triage query
# ---------------------------------------------------------------------------
class TestBuildSprintJql:
    def test_includes_sprint_status_and_labels(self):
        jql = build_sprint_jql("SCRUM", ["To Do"], ["dev-agent"], ["meeting-action-item"])
        assert 'project = "SCRUM"' in jql
        assert "sprint in openSprints()" in jql
        assert 'status in ("To Do")' in jql
        assert 'labels in ("dev-agent")' in jql
        assert 'labels not in ("meeting-action-item")' in jql
        assert jql.rstrip().endswith("ORDER BY created ASC")

    def test_omits_label_clauses_when_empty(self):
        jql = build_sprint_jql("SCRUM", ["To Do"], [], [])
        assert "labels in" not in jql
        assert "labels not in" not in jql
