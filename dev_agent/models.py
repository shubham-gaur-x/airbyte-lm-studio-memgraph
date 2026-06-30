"""Pydantic v2 models for the autonomous dev agent."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


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
