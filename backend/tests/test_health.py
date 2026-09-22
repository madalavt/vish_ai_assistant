"""Health endpoint contract.

These assert the shape the frontend depends on. They do not require Postgres or
Ollama to be up: a degraded status is still a valid response, and asserting
otherwise would make the suite fail for environmental reasons rather than code ones.
"""

import httpx
import pytest
from httpx import ASGITransport

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_returns_expected_shape(client):
    response = await client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert {"database", "ollama"} == set(body["checks"])
    assert all("ok" in check for check in body["checks"].values())


async def test_health_reports_embedding_dim_matching_schema(client):
    """The vector column is declared vector(768); a mismatch here breaks ingestion."""
    body = (await client.get("/api/health")).json()
    assert body["config"]["embedding_dim"] == 768


async def test_cloud_disabled_hides_model_name(client):
    """With no API key set, no cloud model should be advertised to the UI."""
    config = (await client.get("/api/health")).json()["config"]
    if not config["cloud_enabled"]:
        assert config["cloud_model"] is None
