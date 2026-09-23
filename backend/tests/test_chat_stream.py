"""Chat streaming against a scripted provider, so no live model is needed.

The provider is swapped in through `app.api.chat.get_registry`. Everything else
is real: routing, the database, and Starlette's disconnect handling.
"""

import asyncio
import json
import uuid

import httpx
import pytest
from httpx import ASGITransport

from app.api import chat
from app.db import SessionLocal
from app.llm.base import Chunk, ModelInfo
from app.main import app
from app.models.user import LOCAL_USER_ID

MODEL = "scripted:1b"  # supports thinking
OTHER_MODEL = "scripted:2b"  # does not
TOKENS = [f"t{i} " for i in range(40)]
FULL_REPLY = "".join(TOKENS)


class ScriptedProvider:
    name = "scripted"

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.histories: list[list[tuple[str, str]]] = []
        self.think_flags: list[bool] = []

    async def chat_stream(self, messages, model, **opts):
        self.histories.append([(m.role, m.content) for m in messages])
        self.think_flags.append(opts.get("think"))
        for token in TOKENS:
            await asyncio.sleep(self.delay)
            yield Chunk(type="text", text=token)
        yield Chunk(type="done", stop_reason="stop", usage={"output_tokens": len(TOKENS)})


class ScriptedRegistry:
    def __init__(self, provider: ScriptedProvider) -> None:
        self._provider = provider

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(id=MODEL, label=MODEL, provider="scripted", supports_thinking=True),
            ModelInfo(id=OTHER_MODEL, label=OTHER_MODEL, provider="scripted"),
        ]

    async def resolve_chat_model(self, requested: str | None) -> str:
        return requested if requested in (MODEL, OTHER_MODEL) else MODEL

    async def provider_for(self, model: str) -> ScriptedProvider:
        return self._provider


@pytest.fixture
def provider(monkeypatch):
    scripted = ScriptedProvider(delay=0.005)
    monkeypatch.setattr(chat, "get_registry", lambda: ScriptedRegistry(scripted))
    return scripted


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def created(client):
    ids: list[str] = []
    yield ids
    for conversation_id in ids:
        await client.delete(f"/api/conversations/{conversation_id}")


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for frame in body.split("\n\n"):
        lines = frame.splitlines()
        if not lines:
            continue
        name = next(line[6:].strip() for line in lines if line.startswith("event:"))
        data = next(line[5:].strip() for line in lines if line.startswith("data:"))
        events.append((name, json.loads(data)))
    return events


async def send(client, created, content, conversation_id=None, model=MODEL, think=False):
    body = {"conversation_id": conversation_id, "content": content, "think": think}
    if model:
        body["model"] = model
    response = await client.post("/api/chat/stream", json=body)
    assert response.status_code == 200, response.text
    events = parse_sse(response.text)
    start = events[0][1]
    if start["conversation_id"] not in created:
        created.append(start["conversation_id"])
    return start, events


async def saved_messages(client, conversation_id):
    return (await client.get(f"/api/conversations/{conversation_id}")).json()["messages"]


async def hang_up_after(body: dict, tokens: int) -> str:
    """POST through the ASGI app and disconnect after `tokens` token events.

    Goes below httpx because its ASGI transport cannot hang up mid-response.
    Returns what the client received before hanging up.
    """
    received = bytearray()
    hung_up = asyncio.Event()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
        await hung_up.wait()
        return {"type": "http.disconnect"}

    async def send_(message):
        if message["type"] == "http.response.body":
            received.extend(message.get("body", b""))
            if received.count(b"event: token") >= tokens:
                hung_up.set()

    scope = {
        "type": "http",
        # What uvicorn reports, which selects Starlette's cancel-on-disconnect path.
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat/stream",
        "raw_path": b"/api/chat/stream",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    await app(scope, receive, send_)
    return received.decode()


async def test_a_reply_is_streamed_and_saved(client, created, provider):
    start, events = await send(client, created, "[test] hello")

    names = [name for name, _ in events]
    assert names[0] == "start" and names[-1] == "done" and names.count("done") == 1
    user, reply = await saved_messages(client, start["conversation_id"])
    assert (user["role"], user["content"]) == ("user", "[test] hello")
    assert reply["content"] == FULL_REPLY
    assert reply["metadata"]["stop_reason"] == "stop"


async def test_a_disconnect_mid_reply_keeps_what_was_streamed(client, created, provider):
    """Stop in the UI aborts the request. The partial answer must survive it."""
    received = await hang_up_after({"content": "[test] stop me", "model": MODEL}, tokens=3)

    events = parse_sse(received)
    created.append(events[0][1]["conversation_id"])
    streamed = "".join(data["text"] for name, data in events if name == "token")
    assert 0 < len(streamed) < len(FULL_REPLY)

    reply = (await saved_messages(client, events[0][1]["conversation_id"]))[-1]
    assert reply["content"] == streamed
    assert reply["metadata"]["stop_reason"] == chat.CANCELLED


async def test_a_stream_closed_between_tokens_keeps_what_was_streamed(client, created, provider):
    """The other way a stream ends early: closed while suspended at a yield."""
    async with SessionLocal() as session:
        turn = await chat._begin_turn(
            session, uuid.UUID(LOCAL_USER_ID), None, "[test] close me", MODEL
        )
    created.append(str(turn.start.conversation_id))

    stream = chat._event_stream(turn, think=False, temperature=None)
    for _ in range(4):  # start, then three tokens
        await anext(stream)
    await stream.aclose()

    reply = (await saved_messages(client, turn.start.conversation_id))[-1]
    assert reply["content"] == "".join(TOKENS[:3])
    assert reply["metadata"]["stop_reason"] == chat.CANCELLED


async def test_two_sends_at_once_both_land(client, created, provider):
    """The second used to collide on the unique position index and vanish
    behind a 200 with an empty body."""
    start, _ = await send(client, created, "[test] first")
    conversation_id = start["conversation_id"]

    (_, one), (_, two) = await asyncio.gather(
        send(client, created, "[test] one", conversation_id),
        send(client, created, "[test] two", conversation_id),
    )

    assert one[-1][0] == "done" and two[-1][0] == "done"
    saved = await saved_messages(client, conversation_id)
    assert [m["position"] for m in saved] == list(range(6))
    assert sorted(m["content"] for m in saved if m["role"] == "user") == [
        "[test] first",
        "[test] one",
        "[test] two",
    ]
    # The reply still streaming in the other request must not reach the model
    # as an empty assistant turn.
    sent_to_model = [turn for history in provider.histories for turn in history]
    assert all(content for role, content in sent_to_model if role == "assistant")


async def test_new_activity_moves_a_conversation_to_the_top(client, created, provider):
    older, _ = await send(client, created, "[test] older")
    newer, _ = await send(client, created, "[test] newer")

    await send(client, created, "[test] back to the older one", older["conversation_id"])

    ids = [c["id"] for c in (await client.get("/api/conversations")).json()]
    assert ids.index(older["conversation_id"]) < ids.index(newer["conversation_id"])


async def test_editing_the_first_message_keeps_a_chosen_title(client, created, provider):
    start, _ = await send(client, created, "[test] original first line")
    conversation_id = start["conversation_id"]
    await client.patch(f"/api/conversations/{conversation_id}", json={"title": "[test] chosen"})

    await client.post(f"/api/conversations/{conversation_id}/truncate", json={"position": 0})
    await send(client, created, "[test] edited first line", conversation_id)

    detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assert detail["title"] == "[test] chosen"


async def test_editing_the_first_message_retitles_an_untouched_conversation(
    client, created, provider
):
    start, _ = await send(client, created, "[test] original first line")
    conversation_id = start["conversation_id"]

    await client.post(f"/api/conversations/{conversation_id}/truncate", json={"position": 0})
    await send(client, created, "[test] edited first line", conversation_id)

    detail = (await client.get(f"/api/conversations/{conversation_id}")).json()
    assert detail["title"] == "[test] edited first line"


async def test_a_send_without_a_model_stays_on_the_conversations_model(client, created, provider):
    start, _ = await send(client, created, "[test] hi", model=OTHER_MODEL)

    again, _ = await send(client, created, "[test] again", start["conversation_id"], model=None)

    assert again["model"] == OTHER_MODEL


async def test_thinking_is_only_requested_from_models_that_support_it(client, created, provider):
    """Ollama rejects the flag outright for a model without a thinking mode."""
    await send(client, created, "[test] think", model=MODEL, think=True)
    await send(client, created, "[test] think", model=OTHER_MODEL, think=True)

    assert provider.think_flags == [True, False]
