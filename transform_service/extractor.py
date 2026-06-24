from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import openai
import structlog

from transform_service.models import ExtractedMeeting
from transform_service.utils import with_retry

log = structlog.get_logger()

_client: Optional[openai.AsyncOpenAI] = None

_SYSTEM_PROMPT = """You are an expert meeting analyst. Extract structured information from meeting transcripts, emails, and calendar events.

You MUST output ONLY valid JSON matching exactly this schema — no markdown, no explanation, just the JSON object:
{
  "title": "string — concise meeting title",
  "kind": "meeting|email_thread|call|standup|review|other",
  "platform": "string — e.g. Zoom, Google Meet, Slack, Email",
  "date": "YYYY-MM-DD",
  "start_time": "HH:MM or null",
  "end_time": "HH:MM or null",
  "duration_minutes": integer or null,
  "location": "string or null",
  "attendees": [{"name": "string", "email": "string", "role": "host|attendee|organizer"}],
  "summary": "string — 2-3 sentence summary",
  "topics": ["list of topic strings discussed"],
  "decisions": ["list of decisions made"],
  "action_items": [
    {
      "owner": "person name or email",
      "task": "description of task",
      "due": "YYYY-MM-DD or null",
      "done": false,
      "priority": "high|medium|low"
    }
  ],
  "key_quotes": ["notable quotes, max 3"],
  "links": ["URLs mentioned"],
  "sentiment": "positive|neutral|negative|mixed",
  "follow_up_needed": true|false,
  "confidence": 0.0 to 1.0
}

If information is not present, use null or empty arrays. Never invent information not in the source text."""


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            base_url=os.environ["LM_STUDIO_BASE_URL"],
            api_key="lm-studio",
        )
    return _client


@with_retry(max_attempts=3, base_delay=2.0)
async def extract_meeting(
    text: str,
    source_type: str,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[ExtractedMeeting]:
    client = _get_client()
    model = os.environ["LM_STUDIO_MODEL"]
    start = time.monotonic()

    user_prompt = f"Extract meeting information from this {source_type}:\n\n{text}"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            response_format={"type": "text"},
            max_tokens=2000,
        )
    except openai.APIConnectionError as exc:
        log.critical(
            "extractor.lm_studio_unreachable",
            source_type=source_type,
            error=str(exc),
            hint="Is LM Studio running at LM_STUDIO_BASE_URL with gemma3-12b loaded?",
        )
        raise

    duration_ms = int((time.monotonic() - start) * 1000)
    raw = response.choices[0].message.content or ""

    # Strip markdown fences if the model wraps the JSON
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")]
    raw = stripped.strip()

    try:
        data = json.loads(raw)
        ctx = context or {}
        # Fill required fields when LLM returns null
        if not data.get("platform"):
            data["platform"] = ctx.get("platform", "unknown")
        if not data.get("date"):
            fallback_date = ctx.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            data["date"] = fallback_date
        if not data.get("summary"):
            data["summary"] = data.get("title", "No summary available")
        # Sanitize action_items — owner and task must be strings
        for item in data.get("action_items") or []:
            if not item.get("owner"):
                item["owner"] = "Unknown"
            if not item.get("task"):
                item["task"] = "Follow-up required"
        meeting = ExtractedMeeting.model_validate(data)
    except Exception as exc:
        log.error(
            "extractor.parse_failed",
            source_type=source_type,
            duration_ms=duration_ms,
            error=str(exc),
            raw_snippet=raw[:200],
        )
        return None

    log.info(
        "extractor.success",
        source_type=source_type,
        text_length=len(text),
        duration_ms=duration_ms,
        confidence=meeting.confidence,
        title=meeting.title,
    )
    return meeting
