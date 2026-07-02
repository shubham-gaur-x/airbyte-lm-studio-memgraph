import pytest

# Use anyio as the async test runner (anyio is already in the environment)
pytest_plugins = ("anyio",)

# Pin anyio backend to asyncio only. The codebase is asyncio-specific (uvicorn runtime,
# asyncio.create_subprocess_exec in dev_agent). Without this pin, dependency drift can
# re-enable trio parametrization and fail asyncio-only tests unnecessarily.
@pytest.fixture
def anyio_backend():
    return "asyncio"
