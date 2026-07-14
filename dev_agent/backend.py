"""Backend routing and preflight for the dev agent's headless Claude Code runner.

The DEFAULT backend is local LM Studio ($0). A sanctioned, env-gated, opt-in
toggle (``DEV_AGENT_LLM_BACKEND``) lets other users of this repo route the
agent's *coding* work to a hosted model. This applies ONLY to dev-agent code
implementation — meeting-data extraction is always local (see CLAUDE.md).
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import httpx
import structlog

from transform_service.utils import with_retry

log = structlog.get_logger()

VALID_BACKENDS = ("local", "claude", "openrouter", "gemini", "groq")
DEFAULT_MIN_CONTEXT = 32768

# Free hosted tiers route through the LiteLLM proxy; each reads its own key env var.
_HOSTED_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


class PreflightError(RuntimeError):
    """Raised when the selected backend is not ready to run (actionable message)."""


def get_backend() -> str:
    """Read and validate DEV_AGENT_LLM_BACKEND (defaults to ``local``)."""
    backend = os.environ.get("DEV_AGENT_LLM_BACKEND", "local").strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(
            f"Invalid DEV_AGENT_LLM_BACKEND={backend!r}; must be one of {', '.join(VALID_BACKENDS)}"
        )
    return backend


def _lm_studio_root() -> str:
    return os.environ.get("LM_STUDIO_ANTHROPIC_URL", "http://host.docker.internal:1234").rstrip("/")


def resolve_backend_env(backend: str) -> Dict[str, str]:
    """Resolve a backend name to the env overrides for the Claude Code subprocess.

    Invariants (unit-tested for all five values):
      - ``local``:  ANTHROPIC_API_KEY == "" so api.anthropic.com stays unreachable.
      - ``claude``: ANTHROPIC_API_KEY == the real key; routes to api.anthropic.com.
      - hosted tiers: route through the LiteLLM proxy, ANTHROPIC_API_KEY == "".
    """
    if backend == "local":
        return {
            "ANTHROPIC_BASE_URL": _lm_studio_root(),
            "ANTHROPIC_AUTH_TOKEN": "lmstudio",
            "ANTHROPIC_API_KEY": "",
        }
    if backend == "claude":
        return {
            "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
            "ANTHROPIC_AUTH_TOKEN": "",
        }
    if backend in _HOSTED_KEY_ENV:
        return {
            "ANTHROPIC_BASE_URL": os.environ.get("LITELLM_PROXY_URL", "http://litellm:4000"),
            "ANTHROPIC_AUTH_TOKEN": os.environ.get(_HOSTED_KEY_ENV[backend], ""),
            "ANTHROPIC_API_KEY": "",
        }
    raise ValueError(f"Unknown backend {backend!r}")


def select_loaded_model(models: List[dict], min_context: int) -> Tuple[bool, str]:
    """Pure check over LM Studio's /api/v0/models ``data`` array.

    Returns ``(ok, detail)``. A backend is ready when at least one non-embedding
    model has ``state == "loaded"`` and its ``loaded_context_length`` is at least
    ``min_context``.
    """
    loaded = [m for m in models if m.get("state") == "loaded" and m.get("type") != "embeddings"]
    if not loaded:
        return False, "no chat model is loaded in LM Studio"
    best = max(loaded, key=lambda m: m.get("loaded_context_length") or 0)
    ctx = best.get("loaded_context_length") or 0
    if ctx < min_context:
        return False, f"loaded model {best.get('id')!r} context {ctx} < required {min_context}"
    return True, f"{best.get('id')} @ {ctx} ctx"


@with_retry(max_attempts=3, base_delay=2.0)
async def _fetch_models(root: str) -> List[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{root}/api/v0/models")
        resp.raise_for_status()
        return resp.json().get("data", [])


async def preflight_local(min_context: Optional[int] = None) -> str:
    """Verify LM Studio has a chat model loaded with >= min_context.

    Raises :class:`PreflightError` with an actionable message otherwise; returns a
    short description of the loaded model on success.
    """
    if min_context is None:
        min_context = int(os.environ.get("DEV_AGENT_MIN_CONTEXT", str(DEFAULT_MIN_CONTEXT)))
    root = _lm_studio_root()
    try:
        models = await _fetch_models(root)
    except Exception as exc:
        raise PreflightError(
            f"Could not reach LM Studio at {root}/api/v0/models ({exc}). Start LM Studio "
            f"and load a coder model (set DEV_AGENT_LM_MODEL)."
        ) from exc
    ok, detail = select_loaded_model(models, min_context)
    if not ok:
        model = os.environ.get("DEV_AGENT_LM_MODEL") or "a Qwen-family 7B coder model"
        raise PreflightError(
            f"LM Studio preflight failed: {detail}. Load {model} in LM Studio with context "
            f"length >= {min_context} (Developer/Server tab → load model → set context length)."
        )
    log.info("dev_agent.preflight.ok", detail=detail)
    return detail


async def preflight(backend: str, min_context: Optional[int] = None) -> str:
    """Run backend-appropriate preflight. Only ``local`` probes LM Studio."""
    if backend == "local":
        return await preflight_local(min_context)
    if backend == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise PreflightError("DEV_AGENT_LLM_BACKEND=claude requires ANTHROPIC_API_KEY to be set.")
    if backend in _HOSTED_KEY_ENV and not os.environ.get(_HOSTED_KEY_ENV[backend]):
        raise PreflightError(
            f"DEV_AGENT_LLM_BACKEND={backend} requires {_HOSTED_KEY_ENV[backend]} to be set."
        )
    return f"backend={backend} (hosted; no local model preflight)"
