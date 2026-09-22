"""What models the UI may offer."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.llm import get_registry

router = APIRouter(prefix="/api", tags=["providers"])


class ModelOut(BaseModel):
    id: str
    label: str
    provider: str
    kind: str
    context_window: int | None
    local: bool
    parameter_size: str | None = None
    size_bytes: int | None = None
    quantization: str | None = None
    family: str | None = None
    supports_tools: bool = False
    supports_thinking: bool = False


class ProvidersOut(BaseModel):
    models: list[ModelOut]
    default_chat_model: str | None
    embedding_model: str
    # True when at least one non-local model is offered, so the UI can warn
    # that a request would leave the machine.
    cloud_available: bool


@router.get("/providers", response_model=ProvidersOut)
async def list_providers(refresh: bool = False) -> ProvidersOut:
    registry = get_registry()
    settings = get_settings()

    models = await registry.list_models(refresh=refresh)
    chat = [m for m in models if m.kind == "chat"]

    default: str | None = None
    if chat:
        ids = {m.id for m in chat}
        default = settings.default_chat_model if settings.default_chat_model in ids else chat[0].id

    return ProvidersOut(
        models=[
            ModelOut(
                id=m.id,
                label=m.label,
                provider=m.provider,
                kind=m.kind,
                context_window=m.context_window,
                local=m.local,
                parameter_size=m.parameter_size,
                size_bytes=m.size_bytes,
                quantization=m.quantization,
                family=m.family,
                supports_tools=m.supports_tools,
                supports_thinking=m.supports_thinking,
            )
            for m in models
        ],
        default_chat_model=default,
        embedding_model=settings.embedding_model,
        cloud_available=any(not m.local for m in chat),
    )
