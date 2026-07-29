import pytest
import sys

# Use anyio as the async test runner (anyio is already in the environment)
pytest_plugins = ("anyio",)

# Pin anyio backend to asyncio only. The codebase is asyncio-specific (uvicorn runtime,
# asyncio.create_subprocess_exec in dev_agent). Without this pin, dependency drift can
# re-enable trio parametrization and fail asyncio-only tests unnecessarily.
@pytest.fixture
def anyio_backend():
    return "asyncio"


# Several test files stub optional-looking dependencies behind an
# `if mod_name not in sys.modules` guard, written for an environment where the real
# package wasn't installed. All of them ARE installed here, but the guard means whichever
# test file happens to import first "wins" for the rest of the pytest session — a later
# test importing transform_service.main (which needs the real fastapi/structlog/etc.) gets
# a stub with half the attributes missing instead (#B3 — surfaced by test_phase48, which is
# the first to actually import main.py). Generalizes the pre-existing _REAL_HTTPX fix
# (test_phase15_jira_client.py stubs httpx) to every module + submodule seen stubbed
# anywhere under tests/: preload the real ones here, before collection stubs anything, so
# every later `if mod_name not in sys.modules` guard sees "already present" and no-ops.
_REAL_MODULES: dict = {}
for _mod_name in (
    "httpx", "structlog", "asyncpg", "openai", "neo4j", "neo4j.exceptions",
    "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.asyncio",
    "fastapi", "fastapi.middleware", "fastapi.middleware.cors",
):
    try:
        import importlib
        _REAL_MODULES[_mod_name] = importlib.import_module(_mod_name)
    except ImportError:
        pass  # genuinely not installed here — leave test files' own stubs in charge


def pytest_configure(config):
    """Hook: restore every real module captured above, after collection, before tests run."""
    for _mod_name, _mod in _REAL_MODULES.items():
        sys.modules[_mod_name] = _mod
