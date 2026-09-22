"""Shared test fixtures."""

import pytest

from app.db import engine


@pytest.fixture(scope="session", autouse=True)
async def dispose_engine():
    """Close pooled connections at the end of the run.

    Without this the suite exits with 'coroutine Connection._cancel was never
    awaited' warnings from asyncpg connections still held by the pool.
    """
    yield
    await engine.dispose()
