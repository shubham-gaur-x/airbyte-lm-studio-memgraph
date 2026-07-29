"""Pydantic v2 models for the autonomous dev agent."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class DevAgentRun(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_key: str
    status: Literal["queued", "running", "pr_opened", "failed", "skipped"]
    branch_name: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    error: Optional[str] = None
    attempt_count: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Lifecycle state machine (Phase 29). `state` is one of dev_agent.lifecycle.ALL_STATES;
    # `state_payload` carries stage artifacts (design spec, gate results) across resumes.
    state: Optional[str] = None
    state_payload: Dict[str, Any] = {}

    @field_validator("state_payload", mode="before")
    @classmethod
    def _coerce_payload(cls, v: Any) -> Any:
        # asyncpg returns JSONB as a str; accept both str and dict.
        if v is None:
            return {}
        if isinstance(v, str):
            import json

            return json.loads(v) if v.strip() else {}
        return v


class JiraTicket(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    summary: str
    description: str = ""
    status: str = ""
    labels: list[str] = []
    priority: Optional[str] = None


class ClaudeRunResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: bool
    returncode: int
    result_text: str = ""
    num_turns: Optional[int] = None
    duration_ms: int = 0
    timed_out: bool = False
