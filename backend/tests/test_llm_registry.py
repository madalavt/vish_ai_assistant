"""Provider abstraction behaviour.

The registry tests hit a live Ollama; they skip rather than fail when it is not
running, so the suite stays about code rather than environment. The pure-logic
tests below always run.
"""

import pytest

from app.llm import ChatMessage, ProviderError, get_registry
from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.ollama import DOCUMENT_PREFIX, QUERY_PREFIX, OllamaProvider

# --------------------------------------------------------------------- protocol


def test_both_providers_satisfy_the_protocol():
    """If this breaks, a provider has drifted from the interface its callers use."""
    assert isinstance(OllamaProvider(), LLMProvider)
    assert isinstance(AnthropicProvider(), LLMProvider)


# ------------------------------------------------------- Anthropic pure logic


def test_anthropic_disabled_without_a_key():
    provider = AnthropicProvider(api_key="")
    assert provider.enabled is False


async def test_anthropic_offers_nothing_without_a_key():
    """A provider opts out by listing no models, rather than failing at call time."""
    assert await AnthropicProvider(api_key="").available_models() == []


async def test_anthropic_lists_models_with_a_key():
    models = await AnthropicProvider(api_key="sk-ant-fake").available_models()
    assert {m.id for m in models} >= {"claude-sonnet-5", "claude-opus-5"}
    assert all(m.local is False for m in models), "cloud models must be marked non-local"


async def test_anthropic_reports_a_clear_error_when_unconfigured():
    """An unconfigured provider must explain itself, not raise."""
    chunks = [
        c
        async for c in AnthropicProvider(api_key="").chat_stream(
            [ChatMessage(role="user", content="hi")], model="claude-sonnet-5"
        )
    ]
    assert len(chunks) == 1
    assert chunks[0].type == "error"
    assert "ANTHROPIC_API_KEY" in chunks[0].text


def test_system_messages_are_hoisted_out_of_the_turn_list():
    """Anthropic takes system text top-level; Sonnet 5 rejects it mid-conversation.
    Leaving a system turn in `messages` would be an API error at request time."""
    system, turns = AnthropicProvider._split_system(
        [
            ChatMessage(role="system", content="Be terse."),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi"),
            ChatMessage(role="system", content="Also be kind."),
        ]
    )
    assert system == "Be terse.\n\nAlso be kind."
    assert [t["role"] for t in turns] == ["user", "assistant"]


def test_split_system_handles_no_system_messages():
    system, turns = AnthropicProvider._split_system([ChatMessage(role="user", content="Hello")])
    assert system is None
    assert len(turns) == 1


# ------------------------------------------------------------------ embeddings


def test_embedding_prefixes_differ():
    """nomic-embed-text degrades silently if documents and queries share a prefix."""
    assert DOCUMENT_PREFIX != QUERY_PREFIX
    assert DOCUMENT_PREFIX.startswith("search_document")
    assert QUERY_PREFIX.startswith("search_query")


# -------------------------------------------------------------------- registry


@pytest.fixture
async def live_registry():
    registry = get_registry()
    models = await registry.list_models(refresh=True)
    if not [m for m in models if m.kind == "chat"]:
        pytest.skip("Ollama not running; registry tests need a live chat model")
    return registry


async def test_unknown_model_fails_with_the_available_list(live_registry):
    """Failing here, with options, beats failing deep inside a stream."""
    with pytest.raises(ProviderError) as excinfo:
        await live_registry.provider_for("gpt-9-turbo")
    assert "not available" in str(excinfo.value)
    assert "qwen3" in str(excinfo.value)


async def test_resolve_falls_back_when_the_request_is_unusable(live_registry):
    """An unavailable request must degrade to a working model, not error."""
    resolved = await live_registry.resolve_chat_model("some-model-we-never-pulled")
    chat_ids = {m.id for m in await live_registry.chat_models()}
    assert resolved in chat_ids


async def test_resolve_honours_an_available_request(live_registry):
    chat = await live_registry.chat_models()
    assert await live_registry.resolve_chat_model(chat[0].id) == chat[0].id


async def test_embedding_provider_is_always_local(live_registry):
    """Embeddings must not silently go to a cloud provider."""
    assert live_registry.embedding_provider.name == "ollama"
