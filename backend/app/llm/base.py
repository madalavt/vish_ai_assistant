"""The one interface every model goes through.

Chat, RAG answering and workflow LLM nodes depend on `LLMProvider` and nothing
else — no module outside `app/llm/` imports a vendor SDK. That is what makes
swapping models a config change rather than a refactor (ADR 0003).

Keep this protocol as narrow as all callers can share. Provider-specific
options belong in `**opts`, not in new methods.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant", "system"]
ChunkType = Literal["text", "thinking", "done", "error"]


class ProviderError(RuntimeError):
    """Any provider failure, normalized so callers need not know the vendor."""

    def __init__(self, message: str, *, provider: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(slots=True)
class Chunk:
    """One streamed piece of a response.

    `text` carries content for "text" and "thinking". A "done" chunk carries
    `stop_reason` and `usage`; an "error" chunk carries the message in `text`.
    """

    type: ChunkType
    text: str = ""
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelInfo:
    """A model the UI may offer.

    Capability flags are reported rather than assumed: the thinking toggle and
    the M5 workflow LLM node both need to know what a model can actually do,
    and guessing from the model name goes stale the moment a new one is pulled.
    """

    id: str
    label: str
    provider: str
    kind: Literal["chat", "embedding"] = "chat"
    context_window: int | None = None
    # False for cloud models, so the UI can mark what leaves the machine.
    local: bool = True

    # Reported by the provider where available.
    parameter_size: str | None = None
    size_bytes: int | None = None
    quantization: str | None = None
    family: str | None = None
    supports_tools: bool = False
    supports_thinking: bool = False


@runtime_checkable
class LLMProvider(Protocol):
    """Implemented by `OllamaProvider` and `AnthropicProvider`."""

    name: str

    async def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        **opts: Any,
    ) -> AsyncIterator[Chunk]:
        """Yield chunks until a terminal "done" or "error" chunk.

        Implementations must not raise mid-stream: convert failures into an
        "error" chunk so a partially streamed answer is never lost.
        """
        ...

    async def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        """Embed texts. Providers without an embedding API raise ProviderError."""
        ...

    async def available_models(self) -> list[ModelInfo]:
        """Models this provider can actually serve right now.

        Returning an empty list (no API key, nothing pulled) is how a provider
        opts out, rather than failing later at call time.
        """
        ...
