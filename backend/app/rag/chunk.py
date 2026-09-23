"""Split a parsed document into overlapping chunks that keep their anchors.

Three things every chunk must carry, because M4's citations depend on them and
they cannot be recovered later: the page it came from, the section heading above
it, and its character span in the document's full text.

Sizing is in characters, not tokens. There is no cheap local tokenizer for
qwen3, and a fixed ratio is accurate enough for chunk sizing — being 15% off on
a boundary costs nothing, whereas a tokenizer dependency costs an install.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.parse import ParsedBlock, ParsedDocument

# Rough English average. Only used to turn a token target into a character one.
CHARS_PER_TOKEN = 4

DEFAULT_TARGET_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 120

# A chunk may close early at a page boundary once it is at least this full.
# Without it, a chunk spanning three pages gets attributed to the first, and a
# citation for something on the last page points two pages away. Below the
# threshold the boundary is ignored, so short pages do not produce fragments.
PAGE_BREAK_MIN_FILL = 0.45

BLOCK_SEPARATOR = "\n\n"

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(slots=True)
class Chunk:
    text: str
    position: int
    page: int | None
    end_page: int | None
    section: str | None
    start_char: int
    end_char: int


@dataclass(slots=True)
class _Unit:
    """The smallest piece we will not split further when packing."""

    text: str
    start: int
    end: int
    page: int | None
    section: str | None


def _split_with_offsets(text: str, pattern: re.Pattern[str], base: int) -> list[tuple[str, int]]:
    """Split, returning each piece with its absolute offset.

    re.split would lose the offsets, and recomputing them with str.find breaks
    on repeated text.
    """
    pieces: list[tuple[str, int]] = []
    cursor = 0
    for match in pattern.finditer(text):
        piece = text[cursor : match.start()]
        if piece.strip():
            pieces.append((piece, base + cursor))
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        pieces.append((tail, base + cursor))
    return pieces


def _units_for_block(block: ParsedBlock, base: int, max_chars: int) -> list[_Unit]:
    """Paragraphs, falling back to sentences, then a hard cut for runaway text."""
    units: list[_Unit] = []

    for paragraph, offset in _split_with_offsets(block.text, _PARAGRAPH, base):
        if len(paragraph) <= max_chars:
            units.append(
                _Unit(paragraph, offset, offset + len(paragraph), block.page, block.section)
            )
            continue

        for sentence, sentence_offset in _split_with_offsets(paragraph, _SENTENCE, offset):
            if len(sentence) <= max_chars:
                units.append(
                    _Unit(
                        sentence,
                        sentence_offset,
                        sentence_offset + len(sentence),
                        block.page,
                        block.section,
                    )
                )
                continue

            # A single sentence longer than a whole chunk: minified JSON, a
            # table row, OCR noise. Cut it rather than emit an oversized chunk.
            for start in range(0, len(sentence), max_chars):
                piece = sentence[start : start + max_chars]
                units.append(
                    _Unit(
                        piece,
                        sentence_offset + start,
                        sentence_offset + start + len(piece),
                        block.page,
                        block.section,
                    )
                )

    return units


def chunk_document(
    document: ParsedDocument,
    *,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> tuple[list[Chunk], str]:
    """Chunk a document. Returns the chunks and the full text they index into.

    The full text is returned rather than recomputed by the caller, so the
    offsets can never drift from the string they refer to.
    """
    if overlap_tokens >= target_tokens:
        raise ValueError("overlap must be smaller than the target size")

    target_chars = target_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN

    # Build the full text first, recording where each block landed.
    full_text = ""
    units: list[_Unit] = []
    for index, block in enumerate(document.blocks):
        if index:
            full_text += BLOCK_SEPARATOR
        base = len(full_text)
        full_text += block.text
        units.extend(_units_for_block(block, base, target_chars))

    if not units:
        return [], full_text

    chunks: list[Chunk] = []
    window: list[_Unit] = []
    window_chars = 0

    def emit() -> None:
        if not window:
            return
        text = "\n\n".join(u.text.strip() for u in window).strip()
        if not text:
            return
        pages = [u.page for u in window if u.page is not None]
        chunks.append(
            Chunk(
                text=text,
                position=len(chunks),
                page=min(pages) if pages else None,
                end_page=max(pages) if pages else None,
                section=window[0].section,
                start_char=window[0].start,
                end_char=window[-1].end,
            )
        )

    for unit in units:
        crosses_page = bool(window) and unit.page is not None and unit.page != window[-1].page
        full_enough = window_chars >= target_chars * PAGE_BREAK_MIN_FILL

        if window and (
            window_chars + len(unit.text) > target_chars or (crosses_page and full_enough)
        ):
            emit()

            # Carry the tail of the window forward so a chunk boundary cannot
            # sever a sentence from the context that makes it meaningful.
            carried: list[_Unit] = []
            carried_chars = 0
            for previous in reversed(window):
                if carried_chars + len(previous.text) > overlap_chars:
                    break
                # Carrying across a page break would re-attribute the new chunk
                # to the old page, undoing the split above.
                if crosses_page and previous.page != unit.page:
                    break
                carried.insert(0, previous)
                carried_chars += len(previous.text)

            window = carried
            window_chars = carried_chars

        window.append(unit)
        window_chars += len(unit.text)

    emit()
    return chunks, full_text
