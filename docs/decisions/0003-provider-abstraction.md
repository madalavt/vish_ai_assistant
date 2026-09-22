# 0003 — One LLMProvider protocol for all models

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

Models run locally now, but Claude should be selectable when quality matters,
and a future deployment may have no local GPU at all. Scattering
provider-specific calls through chat, RAG and workflow code would make each of
those changes a refactor.

## Decision

Define a single protocol in `backend/app/llm/base.py`:

```python
class LLMProvider(Protocol):
    async def chat_stream(self, messages, model, **opts) -> AsyncIterator[Chunk]: ...
    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...
```

`OllamaProvider` and `AnthropicProvider` implement it. A registry maps a model
id (`qwen3:8b`, `claude-sonnet-5`) to a provider instance, and
`GET /api/providers` exposes what is actually available.

## Consequences

- Chat, RAG answering and every workflow LLM node depend only on this protocol.
- Adding a provider means one new file plus a registry entry.
- The protocol is the narrowest thing that serves all callers; resist widening it
  with provider-specific options. Pass those through `**opts`.
- Models unavailable at runtime (no API key, model not pulled) are filtered out
  of the registry rather than failing at call time.
