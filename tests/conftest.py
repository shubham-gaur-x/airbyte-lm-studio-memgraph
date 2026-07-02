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


# Store real httpx before any test stubs it out (test_phase15_jira_client.py stubs httpx).
# When phase27_action_agent imports airbyte_agent_sdk, it needs the real httpx.AsyncClient.
_REAL_HTTPX = None
try:
    import httpx
    _REAL_HTTPX = httpx
except ImportError:
    pass


def pytest_configure(config):
    """Hook: restore real httpx after collection, before tests run."""
    if _REAL_HTTPX is not None:
        sys.modules["httpx"] = _REAL_HTTPX
