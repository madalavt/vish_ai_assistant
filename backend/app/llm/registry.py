"""Maps a model id to the provider that serves it.

Everything else in the app asks the registry for a provider and then talks to
the `LLMProvider` protocol. Adding a provider means one new module plus one
entry here.
"""

import asyncio
import logging
import time
from collections.abc import Sequence

from app.config import get_settings
from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider, ModelInfo, ProviderError
from app.llm.ollama import OllamaProvider

logger = logging.getLogger(__name__)

# Availability requires network calls, so cache briefly. Short enough that
# pulling a new model shows up without a restart.
_CACHE_TTL_SECONDS = 30.0


class ProviderRegistry:
    def __init__(self) -> None:
        self._ollama = OllamaProvider()
        self._anthropic = AnthropicProvider()
        self._providers: tuple[LLMProvider, ...] = (self._ollama, self._anthropic)

        self._cached: list[ModelInfo] = []
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- models

    async def list_models(self, *, refresh: bool = False) -> list[ModelInfo]:
        """Every model that can actually be served right now."""
        async with self._lock:
            fresh = time.monotonic() - self._cached_at < _CACHE_TTL_SECONDS
            if self._cached and fresh and not refresh:
                return list(self._cached)

            results = await asyncio.gather(
                *(p.available_models() for p in self._providers),
                return_exceptions=True,
            )

            models: list[ModelInfo] = []
            for provider, result in zip(self._providers, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning("provider %s failed to list models: %s", provider.name, result)
                    continue
                models.extend(result)

            self._cached = models
            self._cached_at = time.monotonic()
            return list(models)

    async def chat_models(self) -> list[ModelInfo]:
        return [m for m in await self.list_models() if m.kind == "chat"]

    # ------------------------------------------------------------- resolving

    async def provider_for(self, model: str) -> LLMProvider:
        """The provider that serves `model`.

        Resolution is by declared availability rather than by string prefix, so
        a model that is configured but not pulled fails here with a clear
        message instead of deep inside a stream.
        """
        for info in await self.list_models():
            if info.id == model:
                return self._by_name(info.provider)

        available = ", ".join(m.id for m in await self.list_models()) or "none"
        raise ProviderError(
            f"Model {model!r} is not available. Available: {available}",
            provider="registry",
        )

    def _by_name(self, name: str) -> LLMProvider:
        for provider in self._providers:
            if provider.name == name:
                return provider
        raise ProviderError(f"Unknown provider {name!r}", provider="registry")

    async def resolve_chat_model(self, requested: str | None) -> str:
        """Pick a usable chat model, preferring the request, then the configured
        default, then whatever is actually there."""
        chat = await self.chat_models()
        if not chat:
            raise ProviderError(
                "No chat models available. Is Ollama running? Try: ollama pull qwen3:8b",
                provider="registry",
            )

        ids = {m.id for m in chat}
        settings = get_settings()
        for candidate in (requested, settings.default_chat_model):
            if candidate and candidate in ids:
                return candidate
        return chat[0].id

    # ------------------------------------------------------------ embeddings

    @property
    def embedding_provider(self) -> OllamaProvider:
        """Embeddings are always local — no cloud provider offers them here."""
        return self._ollama

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        from app.llm.ollama import DOCUMENT_PREFIX

        return await self._ollama.embed(
            texts, get_settings().embedding_model, prefix=DOCUMENT_PREFIX
        )

    async def embed_query(self, text: str) -> list[float]:
        from app.llm.ollama import QUERY_PREFIX

        result = await self._ollama.embed(
            [text], get_settings().embedding_model, prefix=QUERY_PREFIX
        )
        return result[0]


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
