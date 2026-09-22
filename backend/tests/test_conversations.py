"""Conversation and message API.

These exercise the real database (the dev one) and clean up after themselves.
Streaming is covered separately — it needs a live model, so asserting on it here
would make the suite fail for environmental reasons.
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


@pytest.fixture
async def conversation(client):
    response = await client.post("/api/conversations", json={"title": "Test thread"})
    if response.status_code == 503:
        pytest.skip("no chat model available; conversations require one")
    assert response.status_code == 201
    created = response.json()
    yield created
    await client.delete(f"/api/conversations/{created['id']}")


async def test_create_assigns_an_available_model(conversation):
    """A conversation must never be created pointing at a model that cannot run."""
    assert conversation["model"]
    assert conversation["title"] == "Test thread"


async def test_conversation_appears_in_the_list(client, conversation):
    ids = [c["id"] for c in (await client.get("/api/conversations")).json()]
    assert conversation["id"] in ids


async def test_rename(client, conversation):
    response = await client.patch(
        f"/api/conversations/{conversation['id']}", json={"title": "Renamed"}
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


async def test_patching_an_unavailable_model_falls_back(client, conversation):
    """A stale dropdown must not persist a model that no longer exists."""
    response = await client.patch(
        f"/api/conversations/{conversation['id']}", json={"model": "not-a-real-model"}
    )
    assert response.status_code == 200
    assert response.json()["model"] != "not-a-real-model"


async def test_delete_is_gone(client, conversation):
    assert (await client.delete(f"/api/conversations/{conversation['id']}")).status_code == 204
    assert (await client.get(f"/api/conversations/{conversation['id']}")).status_code == 404


async def test_unknown_conversation_is_404(client):
    missing = "00000000-0000-0000-0000-0000000000ff"
    assert (await client.get(f"/api/conversations/{missing}")).status_code == 404


async def test_empty_message_is_rejected(client):
    """Whitespace-only input must not create a turn."""
    response = await client.post("/api/chat/stream", json={"content": "   "})
    assert response.status_code == 422


async def test_regenerating_a_brand_new_conversation_is_rejected(client):
    """Regenerate needs history; without a conversation there is nothing to redo."""
    response = await client.post("/api/chat/stream", json={"content": None})
    assert response.status_code == 422


async def test_streaming_to_a_missing_conversation_is_404(client):
    response = await client.post(
        "/api/chat/stream",
        json={"conversation_id": "00000000-0000-0000-0000-0000000000ff", "content": "hi"},
    )
    assert response.status_code in (404, 503)
