"""Headless Claude Code runner for the dev agent."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

import structlog

from dev_agent.models import ClaudeRunResult

log = structlog.get_logger()


async def run_claude_code(
    work_dir: str,
    prompt: str,
    timeout_seconds: int,
    max_turns: int,
    model: Optional[str] = None,
) -> ClaudeRunResult:
    # Build env — copy parent, override LM Studio endpoints.
    # ANTHROPIC_API_KEY is explicitly emptied so a real key in the parent
    # environment can never accidentally route traffic to api.anthropic.com.
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = os.environ.get(
        "LM_STUDIO_ANTHROPIC_URL", "http://host.docker.internal:1234"
    )
    env["ANTHROPIC_AUTH_TOKEN"] = "lmstudio"
    env["ANTHROPIC_API_KEY"] = ""

    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Read,Glob,Grep,Edit,Write,Bash",
        "--permission-mode", "acceptEdits",
        "--output-format", "json",
        "--max-turns", str(max_turns),
    ]
    if model:
        cmd += ["--model", model]

    log.info(
        "claude_runner.start",
        work_dir=work_dir,
        model=model,
        max_turns=max_turns,
    )

    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=work_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout_seconds)
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error("claude_runner.timeout", work_dir=work_dir, timeout_seconds=timeout_seconds)
            return ClaudeRunResult(
                success=False,
                returncode=-1,
                timed_out=True,
                result_text="timed out",
                duration_ms=duration_ms,
            )
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.error("claude_runner.subprocess_error", error=str(exc))
        return ClaudeRunResult(
            success=False,
            returncode=-1,
            result_text=str(exc),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout_str = stdout_bytes.decode(errors="replace")
    stderr_str = stderr_bytes.decode(errors="replace")

    if proc.returncode != 0:
        log.error(
            "claude_runner.nonzero_exit",
            returncode=proc.returncode,
            stderr_snippet=stderr_str[-2000:],
        )
        return ClaudeRunResult(
            success=False,
            returncode=proc.returncode,
            result_text=stderr_str[-2000:],
            duration_ms=duration_ms,
        )

    # Parse the JSON output
    try:
        data = json.loads(stdout_str)
        result_text = data.get("result", "")
        num_turns = data.get("num_turns")
        is_error = bool(data.get("is_error", False))
    except (json.JSONDecodeError, ValueError):
        log.warning(
            "claude_runner.json_parse_failed",
            stdout_snippet=stdout_str[-500:],
        )
        return ClaudeRunResult(
            success=True,
            returncode=0,
            result_text=stdout_str[-2000:],
            duration_ms=duration_ms,
        )

    log.info(
        "claude_runner.finish",
        duration_ms=duration_ms,
        num_turns=num_turns,
        is_error=is_error,
    )
    return ClaudeRunResult(
        success=not is_error,
        returncode=proc.returncode,
        result_text=result_text,
        num_turns=num_turns,
        duration_ms=duration_ms,
    )
