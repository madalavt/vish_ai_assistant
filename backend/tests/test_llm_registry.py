"""Provider abstraction behaviour.

The registry tests hit a live Ollama; they skip rather than fail when it is not
running, so the suite stays about code rather than environment. The pure-logic
tests below always run.
"""

import pytest

from app.llm import ChatMessage, ProviderError, get_registry
from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.ollama import DOCUMENT_PREFIX, QUERY_PREFIX, OllamaProvider, StrayThinkClose

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


# ------------------------------------------------------- stray </think> filter


def _filtered(chunks: list[str]) -> str:
    stray = StrayThinkClose()
    return "".join(stray.feed(c) for c in chunks) + stray.flush()


@pytest.mark.parametrize(
    "chunks",
    [
        ["</think>", "\n\n", "This", " first"],  # as qwen3:1.7b streams it
        ["</", "think", ">", "\n\n", "This first"],  # the tag split across chunks
        ["\n</think>This first"],
    ],
)
def test_a_leading_stray_think_close_is_dropped(chunks):
    assert _filtered(chunks) == "This first"


@pytest.mark.parametrize(
    "chunks",
    [
        ["Hello", "!"],
        ["<", "div> is an element"],  # starts like the tag, then is not
        ["\n", "Indented start"],
        ["Close it with ", "</think>", " like that"],  # mid-reply is content
        ["</thi"],  # ends before it could be the tag
    ],
)
def test_other_replies_pass_through_unchanged(chunks):
    assert _filtered(chunks) == "".join(chunks)


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


# ------------------------------------------------------------ model metadata


def test_label_is_built_from_family_and_size():
    """Labels come from Ollama's metadata so a newly pulled model reads well
    without anyone updating a hardcoded dict."""
    from app.llm.ollama import _label_for

    assert _label_for("llama3.2:3b", "llama", "3.2B") == "Llama 3.2B"
    assert _label_for("qwen3:1.7b", "qwen3", "2.0B") == "Qwen3 2.0B"


def test_label_falls_back_to_the_model_id():
    """An unknown family must not produce an invented name."""
    from app.llm.ollama import _label_for

    assert _label_for("some-new-model:7b", "unheard-of", "7B") == "some-new-model:7b"
    assert _label_for("mystery:1b", None, None) == "mystery:1b"


async def test_embedding_models_are_classified_by_capability(live_registry):
    """Classifying by capability rather than by comparing against the configured
    embedding model means a second embedding model is not misfiled as chat."""
    models = await live_registry.list_models(refresh=True)
    embedding = [m for m in models if m.kind == "embedding"]
    assert embedding, "expected at least one embedding model"
    assert all("embed" in m.id for m in embedding)
    assert all(m.kind == "chat" for m in models if "embed" not in m.id)


async def test_chat_models_report_a_real_context_window(live_registry):
    """A null context window would leave the UI unable to warn about limits."""
    chat = await live_registry.chat_models()
    assert all(m.context_window and m.context_window > 1000 for m in chat)


async def test_thinking_support_is_reported_per_model(live_registry):
    """The composer's thinking toggle is gated on this; a wrong value either
    hides a working feature or offers one that silently does nothing."""
    by_id = {m.id: m for m in await live_registry.chat_models()}

    if "qwen3:8b" in by_id:
        assert by_id["qwen3:8b"].supports_thinking is True
        assert by_id["qwen3:8b"].supports_tools is True
    if "llama3.2:3b" in by_id:
        assert by_id["llama3.2:3b"].supports_thinking is False


async def test_models_are_sorted_smallest_first(live_registry):
    """The picker relies on this ordering to group the fast models together."""
    sizes = [m.size_bytes or 0 for m in await live_registry.list_models(refresh=True)]
    assert sizes == sorted(sizes)
