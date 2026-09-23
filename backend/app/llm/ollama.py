"""Ollama provider: local chat and embeddings over one HTTP server."""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.config import get_settings
from app.llm.base import ChatMessage, Chunk, ModelInfo, ProviderError

logger = logging.getLogger(__name__)

# Display names for models we deliberately ship with. Anything else the user
# pulls is labelled from Ollama's own metadata, so the picker stays correct
# without this dict being kept up to date.
_FAMILY_NAMES = {
    "qwen3": "Qwen3",
    "llama": "Llama",
    "gemma3": "Gemma 3",
    "phi3": "Phi",
    "nomic-bert": "Nomic Embed",
    "granite": "Granite",
    "mistral": "Mistral",
    "smollm2": "SmolLM2",
}


def _label_for(model_id: str, family: str | None, parameter_size: str | None) -> str:
    """A readable name built from Ollama's metadata.

    e.g. ("llama3.2:3b", "llama", "3.2B") -> "Llama 3B". Falls back to the raw
    id, which is always meaningful, rather than inventing something.
    """
    pretty = _FAMILY_NAMES.get((family or "").lower())
    if not pretty:
        return model_id
    if parameter_size:
        return f"{pretty} {parameter_size}"
    return pretty


# nomic-embed-text was trained with task prefixes. Documents and queries must be
# prefixed differently or retrieval quality degrades with no error to show for it.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class StrayThinkClose:
    """Drops the "</think>" qwen3 sometimes streams first with thinking off (seen on 1.7b)."""

    TAG = "</think>"

    def __init__(self) -> None:
        self._head: str | None = ""  # None once past the start
        self._dropped = False

    def feed(self, text: str) -> str:
        if self._head is None:
            return text
        self._head += text
        rest = self._head.lstrip()
        if rest.startswith(self.TAG):
            self._dropped = True
            self._head = rest[len(self.TAG) :]
            rest = self._head.lstrip()
        if not rest or (not self._dropped and self.TAG.startswith(rest)):
            return ""  # undecided: only whitespace, or maybe the tag in pieces
        out = rest if self._dropped else self._head
        self._head = None
        return out

    def flush(self) -> str:
        """Whatever is still held back when the stream ends."""
        out = "" if self._head is None else self._head
        self._head = None
        return out.lstrip() if self._dropped else out


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
        think = bool(opts.get("think", False))
        stray = None if think else StrayThinkClose()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            # qwen3 is a hybrid reasoning model. Thinking is off by default
            # because it roughly doubles latency for everyday chat.
            "think": think,
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
                            if stray and (held := stray.flush()):
                                yield Chunk(type="text", text=held)
                            yield Chunk(type="error", text=str(error))
                            return

                        message = event.get("message") or {}
                        if thinking := message.get("thinking"):
                            yield Chunk(type="thinking", text=thinking)
                        if content := message.get("content"):
                            if stray:
                                content = stray.feed(content)
                            if content:
                                yield Chunk(type="text", text=content)

                        if event.get("done"):
                            if stray and (held := stray.flush()):
                                yield Chunk(type="text", text=held)
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
        """What is actually pulled, described from Ollama's own metadata.

        Empty list if Ollama is down — that is how a provider opts out.
        """
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
            # Ollama reports "qwen3:8b" but "nomic-embed-text:latest".
            short = entry.get("name", "").removesuffix(":latest")
            if not short:
                continue

            details = entry.get("details") or {}
            capabilities = set(entry.get("capabilities") or [])
            family = details.get("family")
            parameter_size = details.get("parameter_size")

            models.append(
                ModelInfo(
                    id=short,
                    label=_label_for(short, family, parameter_size),
                    provider=self.name,
                    # Read the capability rather than comparing against the
                    # configured embedding model: pulling a second embedding
                    # model would otherwise be misfiled as a chat model.
                    kind="embedding" if "embedding" in capabilities else "chat",
                    context_window=details.get("context_length"),
                    local=True,
                    parameter_size=parameter_size,
                    size_bytes=entry.get("size"),
                    quantization=details.get("quantization_level"),
                    family=family,
                    supports_tools="tools" in capabilities,
                    supports_thinking="thinking" in capabilities,
                )
            )

        # Smallest first, so the fast options are easy to find in the picker.
        models.sort(key=lambda m: m.size_bytes or 0)
        return models
