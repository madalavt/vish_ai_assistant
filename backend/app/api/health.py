"""Health endpoint: reports what is actually reachable, not just that the API is up."""

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal

router = APIRouter(tags=["health"])


async def _check_database() -> dict:
    """Confirm Postgres answers and pgvector is installed."""
    try:
        async with SessionLocal() as session:
            version = (await session.execute(text("SHOW server_version"))).scalar_one()
            vector = (
                await session.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar_one_or_none()
        return {
            "ok": vector is not None,
            "server_version": version,
            "pgvector": vector,
            "detail": None if vector else "pgvector extension not installed",
        }
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


async def _check_ollama() -> dict:
    """Confirm Ollama answers and the models we depend on are pulled."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=5.0) as client:
            version = (await client.get("/api/version")).json().get("version")
            tags = (await client.get("/api/tags")).json().get("models", [])

        installed = {m["name"].split(":")[0] for m in tags}
        required = {settings.default_chat_model, settings.embedding_model}
        missing = sorted(r for r in required if r.split(":")[0] not in installed)

        return {
            "ok": not missing,
            "version": version,
            "models": sorted(m["name"] for m in tags),
            "missing": missing,
            "detail": f"run: ollama pull {' '.join(missing)}" if missing else None,
        }
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


@router.get("/api/health")
async def health() -> dict:
    settings = get_settings()
    database = await _check_database()
    ollama = await _check_ollama()

    return {
        "status": "ok" if database["ok"] and ollama["ok"] else "degraded",
        "checks": {"database": database, "ollama": ollama},
        "config": {
            "default_chat_model": settings.default_chat_model,
            "deep_chat_model": settings.deep_chat_model,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "cloud_enabled": settings.cloud_enabled,
            "cloud_model": settings.cloud_chat_model if settings.cloud_enabled else None,
        },
    }
