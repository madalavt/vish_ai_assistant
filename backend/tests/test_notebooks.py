"""Notebook and document API."""

import io

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
async def notebook(client):
    response = await client.post("/api/notebooks", json={"title": "Test notebook"})
    assert response.status_code == 201
    created = response.json()
    yield created
    await client.delete(f"/api/notebooks/{created['id']}")


async def test_create_and_fetch(client, notebook):
    detail = (await client.get(f"/api/notebooks/{notebook['id']}")).json()
    assert detail["title"] == "Test notebook"
    assert detail["documents"] == []
    assert detail["document_count"] == 0


async def test_list_includes_counts(client, notebook):
    """The list drives a progress indicator, so the counts must be present."""
    rows = (await client.get("/api/notebooks")).json()
    mine = next(n for n in rows if n["id"] == notebook["id"])
    assert mine["document_count"] == 0
    assert mine["ready_count"] == 0


async def test_rename(client, notebook):
    response = await client.patch(f"/api/notebooks/{notebook['id']}", json={"title": "Renamed"})
    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"


async def test_delete_is_gone(client, notebook):
    assert (await client.delete(f"/api/notebooks/{notebook['id']}")).status_code == 204
    assert (await client.get(f"/api/notebooks/{notebook['id']}")).status_code == 404


async def test_unknown_notebook_is_404(client):
    missing = "00000000-0000-0000-0000-0000000000ff"
    assert (await client.get(f"/api/notebooks/{missing}")).status_code == 404
    assert (await client.get(f"/api/notebooks/{missing}/documents")).status_code == 404


async def test_unsupported_upload_is_rejected_before_any_row_is_created(client, notebook):
    """Rejecting late would leave an orphan document stuck in error state."""
    response = await client.post(
        f"/api/notebooks/{notebook['id']}/documents",
        files={"file": ("thing.zip", io.BytesIO(b"PK\x03\x04"), "application/zip")},
    )
    assert response.status_code == 415
    assert ".pdf" in response.json()["detail"]

    documents = (await client.get(f"/api/notebooks/{notebook['id']}/documents")).json()
    assert documents == []


async def test_search_on_an_empty_notebook_returns_no_hits(client, notebook):
    """Must not error just because nothing has been ingested yet."""
    response = await client.post(
        f"/api/notebooks/{notebook['id']}/search", json={"query": "anything"}
    )
    if response.status_code == 503:
        pytest.skip("embedding model unavailable")
    assert response.status_code == 200
    assert response.json()["hits"] == []


async def test_empty_search_query_is_rejected(client, notebook):
    response = await client.post(f"/api/notebooks/{notebook['id']}/search", json={"query": ""})
    assert response.status_code == 422


async def test_supported_extensions_are_advertised(client):
    """The upload control reads this to set its accept filter."""
    extensions = (await client.get("/api/documents/supported/extensions")).json()["extensions"]
    assert ".pdf" in extensions
    assert ".docx" in extensions
    assert all(e.startswith(".") for e in extensions)
