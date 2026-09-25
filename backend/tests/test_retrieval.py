"""Hybrid retrieval and grounded answering.

The failure modes here are quiet: a keyword query that matches nothing, a
citation pointing at a source that does not exist, a source filter that does
not actually filter. None of them raise.
"""

import uuid

import httpx
import pytest
from httpx import ASGITransport

from app.main import app
from app.rag.answer import (
    build_messages,
    cited_indices,
    format_sources,
    strip_invalid_citations,
)
from app.rag.retrieve import Retrieved, to_or_tsquery


def make_hit(number: int, **overrides) -> Retrieved:
    defaults = dict(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=f"doc{number}.pdf",
        content=f"Passage {number} content.",
        page=number,
        end_page=number,
        section=None,
        start_char=0,
        end_char=10,
        score=0.01,
        vector_rank=number,
        keyword_rank=None,
    )
    return Retrieved(**{**defaults, **overrides})


# ------------------------------------------------------------------ tsquery


def test_terms_are_ored_not_anded():
    """plainto_tsquery ANDs every term, so a natural-language question matches
    almost nothing and the keyword half contributes nothing at all."""
    query = to_or_tsquery("What pressure should the regulator use")
    assert query is not None
    assert " | " in query
    assert " & " not in query


def test_tsquery_operators_in_user_text_are_stripped():
    """The question is free text, never a query language. Operators must not
    reach the tsquery parser, where they would raise or change the meaning."""
    query = to_or_tsquery("foo' & bar | !baz <-> qux:*")
    assert query == "foo | bar | baz | qux"


def test_tsquery_is_none_when_nothing_is_searchable():
    """An empty tsquery is a syntax error in Postgres, so it must not be built."""
    assert to_or_tsquery("?? !! ...") is None
    assert to_or_tsquery("") is None


def test_single_characters_are_dropped():
    """Single letters match almost everything and only add noise."""
    assert to_or_tsquery("a b regulator") == "regulator"


def test_duplicate_terms_appear_once():
    assert to_or_tsquery("pressure pressure valve") == "pressure | valve"


# ---------------------------------------------------------------- citations


def test_cited_indices_are_in_order_of_first_use_without_repeats():
    assert cited_indices("Yes [2]. Also [1], and again [2].", 3) == [2, 1]


def test_out_of_range_citations_are_ignored():
    """A model inventing [9] for five sources would otherwise produce a
    citation the UI cannot resolve."""
    assert cited_indices("Claim [9] and [2].", 5) == [2]


def test_invalid_markers_are_stripped_from_the_text():
    assert strip_invalid_citations("Real [2], invented [9].", 5) == "Real [2], invented ."


def test_no_citations_is_not_an_error():
    assert cited_indices("An answer with no citations at all.", 4) == []


# ------------------------------------------------------------------ prompting


def test_sources_are_numbered_from_one():
    """The frontend maps [n] back to hits[n-1]; off-by-one here would point
    every citation at the wrong passage."""
    rendered = format_sources([make_hit(1), make_hit(2)])
    assert rendered.startswith("[1] doc1.pdf")
    assert "[2] doc2.pdf" in rendered


def test_prompt_puts_sources_in_the_user_turn():
    """Sources sit next to the question rather than in the system prompt, which
    keeps the system prompt stable for caching on the cloud path."""
    messages = build_messages("What is the pressure?", [make_hit(1)])
    assert messages[0].role == "system"
    assert "Sources:" in messages[-1].content
    assert "What is the pressure?" in messages[-1].content
    assert "Sources:" not in messages[0].content


def test_history_is_included_but_its_sources_are_not():
    from app.llm import ChatMessage

    history = [
        ChatMessage(role="user", content="Earlier question"),
        ChatMessage(role="assistant", content="Earlier answer [1]"),
    ]
    messages = build_messages("Follow-up?", [make_hit(1)], history)
    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert "Earlier question" in messages[1].content


# ----------------------------------------------------------------------- API


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def notebook(client):
    response = await client.post("/api/notebooks", json={"title": "Retrieval test"})
    assert response.status_code == 201
    created = response.json()
    yield created
    await client.delete(f"/api/notebooks/{created['id']}")


async def test_empty_question_is_rejected(client, notebook):
    response = await client.post(
        f"/api/notebooks/{notebook['id']}/chat/stream", json={"content": "   "}
    )
    assert response.status_code == 422


async def test_unknown_notebook_is_404(client):
    missing = "00000000-0000-0000-0000-0000000000ff"
    response = await client.post(f"/api/notebooks/{missing}/chat/stream", json={"content": "hello"})
    assert response.status_code in (404, 503)


async def test_conversations_list_is_scoped_to_the_notebook(client, notebook):
    rows = (await client.get(f"/api/notebooks/{notebook['id']}/conversations")).json()
    assert rows == []


async def test_excluding_every_source_retrieves_nothing(client, notebook):
    """An empty document_ids list means the user switched everything off. It
    must not be treated the same as null, which means 'search everything'."""
    from app.db import SessionLocal
    from app.rag.retrieve import hybrid_search

    async with SessionLocal() as session:
        hits = await hybrid_search(
            session,
            query="anything",
            notebook_id=uuid.UUID(notebook["id"]),
            document_ids=[],
        )
    assert hits == []


# ------------------------------------------------- regressions (m4-tester)


def parse_events(body: str) -> list[str]:
    return [
        line[len("event:") :].strip() for line in body.splitlines() if line.startswith("event:")
    ]


async def test_zero_source_stream_still_ends_with_done(client, notebook):
    """An empty notebook must terminate the stream properly.

    The first implementation returned early, which ran `finally` and killed the
    generator before the trailing `done`. The client uses that terminal event to
    tell a clean finish from a dropped connection, so the user saw a red
    "connection closed" error instead of the no-sources message written for it.
    """
    response = await client.post(
        f"/api/notebooks/{notebook['id']}/chat/stream",
        json={"content": "Is there anything in here?"},
    )
    if response.status_code == 503:
        pytest.skip("no chat model available")
    assert response.status_code == 200

    events = parse_events(response.text)
    assert events[:2] == ["start", "sources"]
    assert events[-1] == "done", f"stream must terminate with done, got {events}"
    assert "could not find anything relevant" in response.text


async def test_notebook_threads_are_absent_from_the_chat_tab(client, notebook):
    """A notebook thread listed in the chat sidebar can be continued there,
    which appends an ungrounded turn to a grounded conversation."""
    response = await client.post(
        f"/api/notebooks/{notebook['id']}/chat/stream", json={"content": "A question"}
    )
    if response.status_code == 503:
        pytest.skip("no chat model available")

    scoped = (await client.get(f"/api/notebooks/{notebook['id']}/conversations")).json()
    assert len(scoped) == 1, "the notebook should list its own thread"

    chat_tab = (await client.get("/api/conversations")).json()
    ids = {c["id"] for c in chat_tab}
    assert scoped[0]["id"] not in ids, "notebook threads must not appear in the chat tab"
    assert all(c.get("notebook_id") is None for c in chat_tab)


async def test_chat_stream_refuses_a_notebook_conversation(client, notebook):
    """The worse half of the same bug: continuing a notebook thread through the
    chat endpoint answers from model knowledge, uncited, inside the tab built
    to prevent exactly that."""
    response = await client.post(
        f"/api/notebooks/{notebook['id']}/chat/stream", json={"content": "A question"}
    )
    if response.status_code == 503:
        pytest.skip("no chat model available")

    conversation_id = (await client.get(f"/api/notebooks/{notebook['id']}/conversations")).json()[
        0
    ]["id"]

    leaked = await client.post(
        "/api/chat/stream",
        json={"conversation_id": conversation_id, "content": "And in general?"},
    )
    assert leaked.status_code == 409
    assert "notebook" in leaked.json()["detail"].lower()


async def test_stored_answer_has_no_out_of_range_citations(client, notebook):
    """What is persisted is read back by any client, not just our frontend,
    so markers pointing at no source are stripped before saving."""
    from app.api.notebook_chat import _save_reply
    from app.schemas.notebook import SourceOut

    source = SourceOut(
        number=1,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="doc.pdf",
        content="text",
        page=1,
        end_page=1,
        section=None,
        start_char=0,
        end_char=4,
        score=0.01,
        vector_rank=1,
        keyword_rank=None,
    )

    response = await client.post(
        f"/api/notebooks/{notebook['id']}/chat/stream", json={"content": "A question"}
    )
    if response.status_code == 503:
        pytest.skip("no chat model available")

    conversation_id = (await client.get(f"/api/notebooks/{notebook['id']}/conversations")).json()[
        0
    ]["id"]
    detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assistant = next(m for m in detail["messages"] if m["role"] == "assistant")

    await _save_reply(
        uuid.UUID(assistant["id"]),
        "Supported [1] but invented [7].",
        [source],
        "stop",
        {},
        None,
    )

    refreshed = (await client.get(f"/api/conversations/{conversation_id}")).json()
    saved = next(m for m in refreshed["messages"] if m["role"] == "assistant")["content"]
    assert "[1]" in saved
    assert "[7]" not in saved


# --------------------------- stale-citation leak across a filter change ----


def test_strip_citations_removes_every_marker():
    from app.rag.answer import strip_citations

    assert strip_citations("It is 47.3 kPa [1]. See also [2].") == "It is 47.3 kPa. See also."


def test_history_assistant_turns_have_their_markers_removed():
    """A replayed [1] refers to a source list that no longer exists. Left in,
    it invites the model to copy the number onto an unrelated passage."""
    from app.llm import ChatMessage

    history = [
        ChatMessage(role="user", content="What pressure? [1]"),
        ChatMessage(role="assistant", content="It is 47.3 kilopascals [1]."),
    ]
    messages = build_messages("And the temperature limit?", [make_hit(1)], history)

    assistant = messages[2]
    assert assistant.role == "assistant"
    assert "[1]" not in assistant.content
    # The user's own words are left alone; they are not a citation claim.
    assert messages[1].content == "What pressure? [1]"


def test_system_prompt_states_that_numbering_resets():
    from app.rag.answer import SYSTEM_PROMPT

    assert "numbering starts" in SYSTEM_PROMPT
    assert "earlier turn" in SYSTEM_PROMPT.lower()


def test_filter_key_normalises_order_and_absence():
    from app.api.notebook_chat import _filter_key

    a, b = uuid.uuid4(), uuid.uuid4()
    assert _filter_key(None) is None
    assert _filter_key([]) == []
    assert _filter_key([a, b]) == _filter_key([b, a]), "order must not look like a change"


async def test_history_is_dropped_when_the_source_filter_changes(client, notebook):
    """The real user path: ask, untick a source, ask a related follow-up.

    The prior answer may assert something this turn's sources no longer
    support. Prompt instructions alone did not stop an 8B model copying it
    forward, so the history is withheld outright.
    """
    from app.api.notebook_chat import _begin_turn
    from app.db import SessionLocal
    from app.models.chat import Conversation, Message
    from app.models.user import LOCAL_USER_ID
    from app.schemas.notebook import NotebookChatRequest

    notebook_id = uuid.UUID(notebook["id"])
    user_id = uuid.UUID(LOCAL_USER_ID)
    first_filter = [str(uuid.uuid4())]

    async with SessionLocal() as session:
        conversation = Conversation(
            user_id=user_id, notebook_id=notebook_id, title="t", model="qwen3:8b"
        )
        session.add(conversation)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="It is 47.3 kilopascals.",
                position=0,
                model="qwen3:8b",
                meta={"document_ids": first_filter},
            )
        )
        await session.commit()
        conversation_id = conversation.id

    async with SessionLocal() as session:
        # Same filter: the earlier turn is still trustworthy context.
        same = await _begin_turn(
            session,
            user_id,
            notebook_id,
            NotebookChatRequest(
                conversation_id=conversation_id,
                content="And the temperature limit?",
                document_ids=[uuid.UUID(first_filter[0])],
            ),
        )
        assert any(m.role == "assistant" for m in same.messages), "history should be kept"

    async with SessionLocal() as session:
        # Different filter: the ground has shifted, so the history goes.
        changed = await _begin_turn(
            session,
            user_id,
            notebook_id,
            NotebookChatRequest(
                conversation_id=conversation_id,
                content="And the temperature limit?",
                document_ids=[uuid.uuid4()],
            ),
        )
        assert not any(m.role == "assistant" for m in changed.messages), (
            "history must be withheld once the searchable sources change"
        )
