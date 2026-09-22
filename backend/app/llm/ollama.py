"""Ollama provider: local chat and embeddings over one HTTP server."""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import ChatMessage, Chunk, ModelInfo, ProviderError

logger = logging.getLogger(__name__)

# Friendly labels for the models we ship with; anything else falls back to its id.
_LABELS = {
    "qwen3:8b": "Qwen3 8B (fast)",
    "qwen3:14b": "Qwen3 14B (deep)",
    "nomic-embed-text": "Nomic Embed",
}

# nomic-embed-text was trained with task prefixes. Documents and queries must be
# prefixed differently or retrieval quality degrades with no error to show for it.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str | None = None, timeout: float = 300.0) -> None:
        settings = get_settings()
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        # Generous: a cold model load on a 14B can take tens of seconds.
        self._timeout = timeout

    def _client(self, timeout: float | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=timeout or self._timeout)

    async def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        **opts: Any,
    ) -> AsyncIterator[Chunk]:
        """Stream a chat completion.

        Ollama returns newline-delimited JSON. Failures become an "error" chunk
        rather than an exception, so a partial answer is never discarded.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            # qwen3 is a hybrid reasoning model. Thinking is off by default
            # because it roughly doubles latency for everyday chat.
            "think": bool(opts.get("think", False)),
            "options": {
                k: v
                for k, v in {
                    "temperature": opts.get("temperature"),
                    "num_ctx": opts.get("num_ctx"),
                    "num_predict": opts.get("max_tokens"),
                    "top_p": opts.get("top_p"),
                }.items()
                if v is not None
            },
        }

        try:
            async with self._client() as client:
                async with client.stream("POST", "/api/chat", json=payload) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode(errors="replace")
                        yield Chunk(
                            type="error",
                            text=f"Ollama returned {response.status_code}: {body[:400]}",
                        )
                        return

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("ollama: unparseable line %r", line[:200])
                            continue

                        if error := event.get("error"):
                            yield Chunk(type="error", text=str(error))
                            return

                        message = event.get("message") or {}
                        if thinking := message.get("thinking"):
                            yield Chunk(type="thinking", text=thinking)
                        if content := message.get("content"):
                            yield Chunk(type="text", text=content)

                        if event.get("done"):
                            yield Chunk(
                                type="done",
                                stop_reason=event.get("done_reason") or "stop",
                                usage={
                                    "input_tokens": event.get("prompt_eval_count"),
                                    "output_tokens": event.get("eval_count"),
                                    "total_duration_ms": (event.get("total_duration") or 0)
                                    // 1_000_000,
                                },
                            )
                            return
        except httpx.HTTPError as exc:
            yield Chunk(type="error", text=f"Cannot reach Ollama at {self._base_url}: {exc}")

    async def embed(
        self, texts: Sequence[str], model: str, *, prefix: str = DOCUMENT_PREFIX
    ) -> list[list[float]]:
        """Embed texts, applying nomic's task prefix.

        Callers should not pass prefixes themselves — `app.rag.store` owns the
        document/query distinction so the two cannot be mixed up at call sites.
        """
        if not texts:
            return []

        prefixed = [f"{prefix}{t}" for t in texts] if prefix else list(texts)

        try:
            async with self._client(timeout=120.0) as client:
                response = await client.post("/api/embed", json={"model": model, "input": prefixed})
                response.raise_for_status()
                embeddings = response.json().get("embeddings")
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Embedding request failed: {exc}", provider=self.name, retryable=True
            ) from exc

        if not embeddings or len(embeddings) != len(texts):
            raise ProviderError(
                f"Expected {len(texts)} embeddings, got {len(embeddings or [])}",
                provider=self.name,
            )
        return embeddings

    async def available_models(self) -> list[ModelInfo]:
        """What is actually pulled. Empty list if Ollama is down."""
        settings = get_settings()
        try:
            async with self._client(timeout=5.0) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
                tags = response.json().get("models", [])
        except httpx.HTTPError:
            logger.warning("ollama: unreachable at %s", self._base_url)
            return []

        models: list[ModelInfo] = []
        for entry in tags:
            raw = entry.get("name", "")
            # Ollama reports "qwen3:8b" but "nomic-embed-text:latest".
            short = raw.removesuffix(":latest")
            is_embedding = short == settings.embedding_model
            models.append(
                ModelInfo(
                    id=short,
                    label=_LABELS.get(short, short),
                    provider=self.name,
                    kind="embedding" if is_embedding else "chat",
                    local=True,
                )
            )
        return models
