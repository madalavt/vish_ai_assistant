"""Grounded answering: answer from retrieved passages, with inline citations.

The prompt is the whole product here. Two failure modes matter more than
fluency: answering from the model's own knowledge when the sources do not
cover the question, and citing a source that does not support the sentence it
is attached to. Both are addressed explicitly rather than hoped away.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.llm import ChatMessage
from app.rag.retrieve import Retrieved

# Matches the [1] / [2] markers the model is asked to emit.
CITATION_PATTERN = re.compile(r"\[(\d{1,2})\]")

SYSTEM_PROMPT = """\
You answer questions using only the numbered sources provided below.

Rules:
- Use only what the sources say. Do not add facts from your own knowledge, \
even if you are confident they are correct.
- The sources are re-selected for every question, and the numbering starts \
again each time. Numbers used earlier in this conversation do not refer to the \
sources below.
- Earlier turns are provided only so you can interpret the current question. \
Never repeat a fact from an earlier turn unless one of the sources below also \
states it. If an earlier answer covered something the current sources do not, \
say that the current sources do not cover it.
- Cite with bracketed numbers that match the source numbers, like [1] or [2]. \
Put the citation immediately after the claim it supports, not at the end of \
the answer.
- Cite a source only if it actually supports that specific claim.
- If the sources do not answer the question, say so plainly and state what \
they do cover. Do not guess, and do not pad the answer.
- If sources disagree, say so and cite each side.
- Be concise. Do not restate the question or describe what you are about to do.
"""

NO_SOURCES_MESSAGE = (
    "I could not find anything relevant in this notebook's sources. "
    "Try rephrasing, or check that the documents you expected are added and finished processing."
)


def format_sources(hits: Sequence[Retrieved]) -> str:
    """Render retrieved passages as a numbered block the model can cite.

    Numbering is 1-based and positional: source [1] is hits[0]. The frontend
    relies on that to turn a marker back into a passage.
    """
    blocks = []
    for index, hit in enumerate(hits, start=1):
        blocks.append(f"[{index}] {hit.cite()}\n{hit.content.strip()}")
    return "\n\n".join(blocks)


def build_messages(
    question: str,
    hits: Sequence[Retrieved],
    history: Sequence[ChatMessage] = (),
) -> list[ChatMessage]:
    """Assemble the request.

    Sources go in the user turn rather than the system prompt so they sit
    close to the question; models attend to them more reliably there, and it
    keeps the system prompt stable for prompt caching on the cloud path.
    """
    messages: list[ChatMessage] = [ChatMessage(role="system", content=SYSTEM_PROMPT)]

    # Prior turns give follow-ups like "why?" something to refer to. Their
    # sources are deliberately not re-included; only this turn's are citable.
    #
    # Assistant turns have their markers stripped. With them left in, changing
    # which sources are ticked and asking a related follow-up made the model
    # copy the earlier fact *and* its [1] onto this turn's source list — a
    # citation pointing at a passage that does not support the claim.
    messages.extend(
        ChatMessage(
            role=m.role,
            content=strip_citations(m.content) if m.role == "assistant" else m.content,
        )
        for m in history
    )

    messages.append(
        ChatMessage(
            role="user",
            content=(
                f"Sources:\n\n{format_sources(hits)}\n\n"
                f"Question: {question}\n\n"
                "Answer using only these sources, citing them inline."
            ),
        )
    )
    return messages


def strip_citations(text: str) -> str:
    """Remove every citation marker.

    Used on prior assistant turns before replaying them. Their numbers refer to
    a source list that no longer exists, so leaving them in invites the model to
    copy a number straight out of the transcript onto a passage that does not
    support the claim.
    """
    return re.sub(r"\s*" + CITATION_PATTERN.pattern, "", text)


def cited_indices(answer: str, source_count: int) -> list[int]:
    """The 1-based source numbers actually cited, in order of first appearance.

    Markers outside the valid range are dropped: a model that invents [7] for
    five sources would otherwise produce a citation the UI cannot resolve.
    """
    seen: list[int] = []
    for match in CITATION_PATTERN.finditer(answer):
        number = int(match.group(1))
        if 1 <= number <= source_count and number not in seen:
            seen.append(number)
    return seen


def strip_invalid_citations(answer: str, source_count: int) -> str:
    """Remove citation markers that point at no source.

    Leaving them in renders a dead link. Dropping the marker keeps the prose
    readable while making clear the claim is uncited.
    """

    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return match.group(0) if 1 <= number <= source_count else ""

    return CITATION_PATTERN.sub(replace, answer)
