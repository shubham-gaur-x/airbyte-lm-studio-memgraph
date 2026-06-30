from __future__ import annotations

from datetime import date, time
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class Attendee(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    email: Optional[str] = None
    role: str = "attendee"


class ActionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner: str
    task: str
    due: Optional[date] = None
    done: bool = False
    priority: Literal["high", "medium", "low"] = "medium"
    jira_key: Optional[str] = None


class ExtractedMeeting(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    kind: Literal["meeting", "email_thread", "call", "standup", "review", "other"]
    platform: str
    date: date
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    duration_minutes: Optional[int] = None
    location: Optional[str] = None
    attendees: List[Attendee] = []
    summary: str
    topics: List[str] = []
    decisions: List[str] = []
    action_items: List[ActionItem] = []
    key_quotes: List[str] = []
    links: List[str] = []
    sentiment: Literal["positive", "neutral", "negative", "mixed"] = "neutral"
    follow_up_needed: bool = False
    confidence: float = 0.0


class RawEmail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    source_id: str
    subject: str
    from_email: str
    to_emails: List[str]
    body: str
    received_at: str
    processed: bool = False
    source_table: str = "raw_emails"


class RawCalendarEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    source_id: str
    title: str
    description: Optional[str] = None
    start_time: str
    end_time: str
    attendees_json: Optional[str] = None
    processed: bool = False
    source_table: str = "raw_calendar_events"


class RawJiraIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    source_id: str
    key: str
    summary: str
    status: str
    assignee: Optional[str] = None
    priority: Optional[str] = None
    jira_created_at: Optional[str] = None
    jira_updated_at: Optional[str] = None
    processed: bool = False


class AirbyteWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    connection_id: str
    status: str
    job_id: Optional[str] = None
