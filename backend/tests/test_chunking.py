"""Chunker behaviour.

Chunking bugs do not raise — they quietly produce worse retrieval or citations
that point at the wrong page, so these assert the properties that would
otherwise fail silently.
"""

import pytest

from app.rag.chunk import CHARS_PER_TOKEN, chunk_document
from app.rag.parse import ParsedBlock, ParsedDocument, ParseError, parser_for, split_into_sections


def make_doc(blocks: list[ParsedBlock]) -> ParsedDocument:
    return ParsedDocument(title="test", blocks=blocks)


# ------------------------------------------------------------------- offsets


def test_offsets_resolve_into_the_returned_full_text():
    """start_char/end_char are the anchors M4 uses; if they drift, a citation
    highlights the wrong passage with no error to show for it."""
    doc = make_doc(
        [ParsedBlock(text=f"Paragraph {i} with some content. " * 12, page=i) for i in range(1, 8)]
    )
    chunks, full_text = chunk_document(doc)

    assert chunks
    for chunk in chunks:
        assert 0 <= chunk.start_char < chunk.end_char <= len(full_text)
        span = full_text[chunk.start_char : chunk.end_char]
        # The chunk joins units with blank lines, so compare on first words.
        assert chunk.text.split()[0] in span


def test_repeated_text_does_not_confuse_offsets():
    """Identical paragraphs would break any offset scheme based on str.find."""
    identical = "The same sentence repeated. " * 10
    doc = make_doc([ParsedBlock(text=identical, page=1), ParsedBlock(text=identical, page=2)])
    chunks, full_text = chunk_document(doc, target_tokens=100, overlap_tokens=20)

    starts = [c.start_char for c in chunks]
    assert starts == sorted(starts), "offsets must advance monotonically"
    assert len(set(starts)) == len(starts), "each chunk needs a distinct offset"
    assert full_text.count(identical.strip()[:20]) >= 2


# --------------------------------------------------------------------- pages


def test_page_range_is_recorded_not_just_the_first_page():
    """A chunk spanning two short pages must say so, or a citation for text on
    the second page points at the first."""
    doc = make_doc([ParsedBlock(text=f"Short page {p}. " * 30, page=p) for p in range(1, 9)])
    chunks, _ = chunk_document(doc)

    assert all(c.page is not None and c.end_page is not None for c in chunks)
    assert all(c.end_page >= c.page for c in chunks)
    assert any(c.end_page > c.page for c in chunks), "expected a chunk to span short pages"


def test_dense_pages_produce_one_chunk_per_page():
    """Real documents have ~3000 characters a page, which should land 1:1 so
    citations name an exact page."""
    doc = make_doc([ParsedBlock(text="Dense content here. " * 150, page=p) for p in range(1, 6)])
    chunks, _ = chunk_document(doc)

    assert all(c.page == c.end_page for c in chunks), "dense pages should not be merged"
    assert {c.page for c in chunks} == {1, 2, 3, 4, 5}


def test_pages_without_numbers_are_handled():
    """docx and plain text have no pages; that must not crash the chunker."""
    doc = make_doc([ParsedBlock(text="Text without pages. " * 40, page=None)])
    chunks, _ = chunk_document(doc)

    assert chunks
    assert all(c.page is None and c.end_page is None for c in chunks)


# ------------------------------------------------------------------- overlap


def test_consecutive_chunks_overlap():
    """Overlap is what keeps a sentence from being severed from the context
    that makes it findable."""
    doc = make_doc([ParsedBlock(text="Sentence number one here. " * 200, page=1)])
    chunks, _ = chunk_document(doc, target_tokens=200, overlap_tokens=50)

    assert len(chunks) > 2
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert later.start_char < earlier.end_char, "chunks must overlap"


def test_overlap_larger_than_target_is_rejected():
    """It would loop forever rather than fail, so it is checked up front."""
    doc = make_doc([ParsedBlock(text="Text. " * 100)])
    with pytest.raises(ValueError):
        chunk_document(doc, target_tokens=100, overlap_tokens=100)


# ---------------------------------------------------------------------- size


def test_no_chunk_greatly_exceeds_the_target():
    """An oversized chunk silently truncates at the embedding model's limit."""
    target = 200
    doc = make_doc([ParsedBlock(text="Regular sentence content. " * 300, page=1)])
    chunks, _ = chunk_document(doc, target_tokens=target, overlap_tokens=40)

    limit = target * CHARS_PER_TOKEN * 1.5
    assert all(len(c.text) <= limit for c in chunks)


def test_a_single_giant_sentence_is_split():
    """Minified JSON and table rows have no sentence breaks at all."""
    doc = make_doc([ParsedBlock(text="x" * 20_000, page=1)])
    chunks, _ = chunk_document(doc, target_tokens=200, overlap_tokens=40)

    assert len(chunks) > 1
    assert all(len(c.text) <= 200 * CHARS_PER_TOKEN * 1.5 for c in chunks)


def test_empty_document_yields_no_chunks():
    chunks, full_text = chunk_document(make_doc([]))
    assert chunks == []
    assert full_text == ""


def test_positions_are_sequential():
    """Position is the unique key with document_id; a gap or repeat breaks the
    unique index at insert time."""
    doc = make_doc([ParsedBlock(text="Content here. " * 200, page=1)])
    chunks, _ = chunk_document(doc, target_tokens=150, overlap_tokens=30)
    assert [c.position for c in chunks] == list(range(len(chunks)))


# -------------------------------------------------------------------- parsing


def test_headings_become_sections():
    blocks = split_into_sections("# Title\n\nBody text.\n\n## Sub\n\nMore text.", page=3)
    assert [b.section for b in blocks] == ["Title", "Sub"]
    assert all(b.page == 3 for b in blocks)


def test_text_without_headings_is_one_block():
    blocks = split_into_sections("Just some prose with no headings at all.")
    assert len(blocks) == 1
    assert blocks[0].section is None


def test_parser_is_selected_by_extension():
    assert parser_for("report.pdf").name == "pymupdf4llm"
    assert parser_for("report.docx").name == "python-docx"
    assert parser_for("NOTES.MD").name == "text"


def test_unsupported_extension_names_what_is_supported():
    with pytest.raises(ParseError) as excinfo:
        parser_for("archive.zip")
    assert ".pdf" in str(excinfo.value)
