"""Phase 50 (B5): transcript ingestion + Meet-pull run on an interval, not only on the
Airbyte webhook. process_new_transcripts (P1) previously only fired from
/webhook/airbyte's background_tasks — a transcript staged directly by meet_ingest (its own
Pub/Sub-pull consumer) had nothing draining it until the next unrelated Airbyte sync.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from transform_service import main


@pytest.mark.anyio
async def test_poll_meet_transcripts_pulls_and_processes():
    with patch.object(main.meet_ingest, "pull_and_stage", AsyncMock(return_value=2)) as mock_pull:
        await main._poll_meet_transcripts()
    mock_pull.assert_awaited_once()
    # process_new_transcripts is passed through so a successful pull drains immediately,
    # not just on the next independent poll_transcripts tick.
    assert mock_pull.await_args.kwargs.get("process") is main.process_new_transcripts


@pytest.mark.anyio
async def test_lifespan_registers_transcript_and_meet_pull_jobs():
    job_ids = []

    def _add_job(func, *args, **kwargs):
        job_ids.append(kwargs.get("id"))

    with (
        patch.object(main.db, "create_staging_tables", AsyncMock()),
        patch.object(main.memgraph_client, "create_indexes", AsyncMock()),
        patch.object(main.memgraph_client, "close_driver", AsyncMock()),
        patch.object(main.scheduler, "add_job", side_effect=_add_job),
        patch.object(main.scheduler, "start", lambda: None),
        patch.object(main.scheduler, "shutdown", lambda wait=True: None),
    ):
        async with main.lifespan(main.app):
            pass

    assert "poll_transcripts" in job_ids
    assert "poll_meet_pull" in job_ids
