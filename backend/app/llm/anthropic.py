"""Anthropic provider: the cloud escape hatch for when local quality is not enough.

Disabled entirely when ANTHROPIC_API_KEY is blank — `available_models()` returns
an empty list and the model never appears in the UI, rather than failing at call
time.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.config import get_settings
from app.llm.base import ChatMessage, Chunk, ModelInfo, ProviderError

logger = logging.getLogger(__name__)

# Curated rather than pulled from the Models API: this is a personal assistant,
# and an exhaustive list of every published model is noise in a dropdown.
# Prices are $/1M tokens (input/output) as of 2026-09.
_MODELS = [
    ModelInfo(
        id="claude-sonnet-5",
        label="Claude Sonnet 5 (cloud)",
        provider="anthropic",
        context_window=1_000_000,
        local=False,
    ),
    ModelInfo(
        id="claude-opus-5",
        label="Claude Opus 5 (cloud, premium)",
        provider="anthropic",
        context_window=1_000_000,
        local=False,
    ),
    ModelInfo(
        id="claude-haiku-4-5",
        label="Claude Haiku 4.5 (cloud, cheap)",
        provider="anthropic",
        context_window=200_000,
        local=False,
    ),
]

# Streaming, so a large ceiling costs nothing and avoids truncating mid-thought.
DEFAULT_MAX_TOKENS = 64_000


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = (api_key or settings.anthropic_api_key).strip()
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _get_client(self) -> Any:
        """Built lazily so an unset key never costs an import or a connection."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - install-time issue
                raise ProviderError(
                    "anthropic package not installed; run: uv sync --extra cloud",
                    provider=self.name,
                ) from exc
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _split_system(
        messages: Sequence[ChatMessage],
    ) -> tuple[str | None, list[dict[str, str]]]:
        """Anthropic takes system text as a top-level parameter.

        Sonnet 5 does not support mid-conversation system messages, so all
        system turns are collapsed into one top-level prompt.
        """
        system_parts = [m.content for m in messages if m.role == "system"]
        turns = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        return ("\n\n".join(system_parts) or None), turns

    async def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: str,
        **opts: Any,
    ) -> AsyncIterator[Chunk]:
        if not self.enabled:
            yield Chunk(
                type="error",
                text="Claude is not configured. Set ANTHROPIC_API_KEY in .env to enable it.",
            )
            return

        import anthropic

        system, turns = self._split_system(messages)
        if not turns:
            yield Chunk(type="error", text="A conversation needs at least one non-system message.")
            return

        request: dict[str, Any] = {
            "model": model,
            "max_tokens": opts.get("max_tokens") or DEFAULT_MAX_TOKENS,
            "messages": turns,
        }
        if system:
            request["system"] = system
        if (temperature := opts.get("temperature")) is not None:
            request["temperature"] = temperature
        if opts.get("think"):
            # Adaptive is the only on-mode on current models; budget_tokens is
            # rejected. display must be opted into or thinking text arrives empty.
            request["thinking"] = {"type": "adaptive", "display": "summarized"}
        if (effort := opts.get("effort")) is not None:
            request["output_config"] = {"effort": effort}

        try:
            client = self._get_client()
            async with client.messages.stream(**request) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "thinking_delta":
                        yield Chunk(type="thinking", text=delta.thinking)
                    elif delta.type == "text_delta":
                        yield Chunk(type="text", text=delta.text)

                final = await stream.get_final_message()

            # A policy decline returns HTTP 200 with no usable content. Without
            # this branch it would look like an empty answer.
            if final.stop_reason == "refusal":
                detail = getattr(final, "stop_details", None)
                explanation = getattr(detail, "explanation", None) or "no explanation given"
                yield Chunk(
                    type="error",
                    text=f"Claude declined this request ({explanation}). Try the local model.",
                )
                return

            yield Chunk(
                type="done",
                stop_reason=final.stop_reason,
                usage={
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                    "model": final.model,
                },
            )

        # Most specific first; APIConnectionError is a sibling of APIStatusError
        # in the Python SDK, so it needs its own branch.
        except anthropic.NotFoundError as exc:
            yield Chunk(type="error", text=f"Unknown model {model!r}: {exc}")
        except anthropic.RateLimitError as exc:
            yield Chunk(type="error", text=f"Rate limited by Anthropic; retry shortly. ({exc})")
        except anthropic.APIStatusError as exc:
            yield Chunk(type="error", text=f"Anthropic API error {exc.status_code}: {exc}")
        except anthropic.APIConnectionError as exc:
            yield Chunk(type="error", text=f"Could not reach Anthropic: {exc}")

    async def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        """Anthropic has no embeddings endpoint; embeddings stay local."""
        raise ProviderError(
            "Anthropic does not provide embeddings; use the Ollama provider.",
            provider=self.name,
        )

    async def available_models(self) -> list[ModelInfo]:
        return list(_MODELS) if self.enabled else []
