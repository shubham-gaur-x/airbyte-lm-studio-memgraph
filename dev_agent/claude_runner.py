"""Headless Claude Code runner for the dev agent."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

import structlog

from dev_agent.backend import get_backend, resolve_backend_env
from dev_agent.models import ClaudeRunResult

log = structlog.get_logger()


async def run_oneshot(
    prompt: str,
    timeout_seconds: int,
    model: Optional[str] = None,
) -> Optional[str]:
    """Run a single-turn, no-tools ``claude -p`` through the selected backend.

    Returns the model's answer text (the JSON ``result`` field) or None on failure.
    For cheap scoring passes (e.g. P8 self-verification), NOT for code work — hence no
    tools, no work_dir, and ``--max-turns 1``.
    """
    backend = get_backend()
    env = os.environ.copy()
    env.update(resolve_backend_env(backend))

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--max-turns", "1"]
    if model:
        cmd += ["--model", model]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(), timeout=float(timeout_seconds)
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("claude_runner.oneshot_timeout", timeout_seconds=timeout_seconds)
        return None
    except Exception as exc:
        log.warning("claude_runner.oneshot_error", error=str(exc))
        return None

    out = stdout_bytes.decode(errors="replace")
    try:
        return json.loads(out).get("result", "") or None
    except (json.JSONDecodeError, ValueError):
        return out or None


async def run_claude_code(
    work_dir: str,
    prompt: str,
    timeout_seconds: int,
    max_turns: int,
    model: Optional[str] = None,
) -> ClaudeRunResult:
    # Build env — copy parent, then overlay the selected backend's routing.
    # Default backend is local LM Studio; in local mode ANTHROPIC_API_KEY is
    # emptied so a real key in the parent env can never route to api.anthropic.com.
    # Hosted backends are an opt-in exception (DEV_AGENT_LLM_BACKEND) for coding only.
    backend = get_backend()
    env = os.environ.copy()
    env.update(resolve_backend_env(backend))

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
        # claude's own JSON error result (the actually useful message, e.g.
        # LM Studio API errors) is written to stdout even on nonzero exit —
        # stderr is frequently empty. Prefer the parsed "result" field, fall
        # back to raw stdout, then stderr, so a real message always surfaces.
        error_detail = stderr_str.strip()
        if not error_detail and stdout_str.strip():
            try:
                parsed = json.loads(stdout_str)
                error_detail = parsed.get("result") or stdout_str
            except (json.JSONDecodeError, ValueError):
                error_detail = stdout_str

        log.error(
            "claude_runner.nonzero_exit",
            returncode=proc.returncode,
            error_detail=error_detail[-2000:],
        )
        return ClaudeRunResult(
            success=False,
            returncode=proc.returncode or 0,
            result_text=error_detail[-2000:],
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
