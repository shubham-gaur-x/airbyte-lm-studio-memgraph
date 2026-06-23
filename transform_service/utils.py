from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from datetime import date
from typing import Any, Callable, Optional, TypeVar

import structlog

F = TypeVar("F", bound=Callable[..., Any])

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def uuid5_id(namespace: str, value: str) -> str:
    ns = uuid.uuid5(_NAMESPACE, namespace)
    return str(uuid.uuid5(ns, value))


def configure_logging() -> structlog.BoundLogger:
    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    return structlog.get_logger()


def with_retry(max_attempts: int = 3, base_delay: float = 2.0) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = structlog.get_logger()
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts:
                        raise
                    delay = base_delay * (2 ** (attempt - 1))
                    log.warning(
                        "retry",
                        fn=fn.__name__,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        delay=delay,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)

        return wrapper  # type: ignore[return-value]

    return decorator


def priority_from_due(due: Optional[date]) -> str:
    if due is None:
        return "low"
    delta = (due - date.today()).days
    if delta <= 14:
        return "high"
    if delta <= 60:
        return "medium"
    return "low"
