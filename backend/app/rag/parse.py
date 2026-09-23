"""Turn a source file or URL into text blocks that carry citation anchors.

Parsers sit behind one interface, the same way models sit behind LLMProvider.
Swapping in a heavier parser — docling, say, for table-dense PDFs — means one
new class and one registry entry, with nothing downstream changing.

pymupdf4llm is the default because it is a C library with no ML models: ~30 MB
installed, no torch, and no RAM contention with the chat model during ingestion.
See ADR 0005.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Markdown ATX headings, used to attribute text to a section for citations.
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class ParseError(RuntimeError):
    """Raised when a source cannot be read at all."""


@dataclass(slots=True)
class ParsedBlock:
    """A contiguous run of text with wherever it came from attached."""

    text: str
    page: int | None = None
    section: str | None = None


@dataclass(slots=True)
class ParsedDocument:
    title: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    # Parser-specific detail: page count, parser name, detected language.
    # Lands in documents.source_meta as JSONB rather than new columns.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


def split_into_sections(text: str, page: int | None = None) -> list[ParsedBlock]:
    """Split markdown into blocks, carrying the nearest heading as the section.

    Page-sized blocks would be too coarse for citations, and splitting on
    headings keeps a chunk attributable to something a reader can find.
    """
    if not text.strip():
        return []

    blocks: list[ParsedBlock] = []
    current_section: str | None = None
    cursor = 0

    for match in _HEADING.finditer(text):
        body = text[cursor : match.start()].strip()
        if body:
            blocks.append(ParsedBlock(text=body, page=page, section=current_section))
        current_section = match.group(2).strip()
        cursor = match.end()

    tail = text[cursor:].strip()
    if tail:
        blocks.append(ParsedBlock(text=tail, page=page, section=current_section))

    # No headings at all: the whole thing is one block.
    if not blocks:
        blocks.append(ParsedBlock(text=text.strip(), page=page, section=None))

    return blocks


class Parser(Protocol):
    name: str
    extensions: tuple[str, ...]

    def parse(self, path: Path, *, title: str) -> ParsedDocument: ...


class PdfParser:
    """PyMuPDF via pymupdf4llm: markdown per page, with page numbers preserved."""

    name = "pymupdf4llm"
    extensions = (".pdf",)

    def parse(self, path: Path, *, title: str) -> ParsedDocument:
        import pymupdf4llm

        try:
            pages = pymupdf4llm.to_markdown(str(path), page_chunks=True)
        except Exception as exc:
            raise ParseError(f"Could not read the PDF: {exc}") from exc

        blocks: list[ParsedBlock] = []
        page_count = 0
        for page in pages:
            meta = page.get("metadata") or {}
            number = meta.get("page_number")
            page_count = meta.get("page_count") or page_count
            blocks.extend(split_into_sections(page.get("text") or "", page=number))

        if not blocks:
            raise ParseError(
                "No text found. This may be a scanned PDF, which needs OCR (not supported yet)."
            )

        return ParsedDocument(
            title=title,
            blocks=blocks,
            meta={"parser": self.name, "page_count": page_count},
        )


class DocxParser:
    name = "python-docx"
    extensions = (".docx",)

    def parse(self, path: Path, *, title: str) -> ParsedDocument:
        import docx

        try:
            document = docx.Document(str(path))
        except Exception as exc:
            raise ParseError(f"Could not read the Word document: {exc}") from exc

        blocks: list[ParsedBlock] = []
        current_section: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            body = "\n\n".join(buffer).strip()
            if body:
                blocks.append(ParsedBlock(text=body, section=current_section))
            buffer.clear()

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            # Word marks headings by style name, e.g. "Heading 1".
            if paragraph.style is not None and paragraph.style.name.startswith("Heading"):
                flush()
                current_section = text
                continue
            buffer.append(text)
        flush()

        # Tables become pipe rows; crude, but keeps their content searchable.
        for table in document.tables:
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            body = "\n".join(r for r in rows if r.strip(" |"))
            if body:
                blocks.append(ParsedBlock(text=body, section=current_section))

        if not blocks:
            raise ParseError("The document appears to be empty.")

        return ParsedDocument(
            title=title,
            blocks=blocks,
            meta={"parser": self.name, "paragraphs": len(document.paragraphs)},
        )


class TextParser:
    name = "text"
    extensions = (".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".log")

    def parse(self, path: Path, *, title: str) -> ParsedDocument:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ParseError(f"Could not read the file: {exc}") from exc

        blocks = split_into_sections(text)
        if not blocks:
            raise ParseError("The file is empty.")

        return ParsedDocument(
            title=title, blocks=blocks, meta={"parser": self.name, "characters": len(text)}
        )


_PARSERS: tuple[Parser, ...] = (PdfParser(), DocxParser(), TextParser())

SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(
    sorted({ext for parser in _PARSERS for ext in parser.extensions})
)


def parser_for(filename: str) -> Parser:
    suffix = Path(filename).suffix.lower()
    for parser in _PARSERS:
        if suffix in parser.extensions:
            return parser
    raise ParseError(
        f"Unsupported file type {suffix or '(none)'}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
    )


def parse_file(path: Path, *, filename: str, title: str) -> ParsedDocument:
    """Parse a file by extension."""
    return parser_for(filename).parse(path, title=title)


def parse_url(url: str, *, title: str | None = None) -> ParsedDocument:
    """Fetch a page and keep the article, not the navigation and footers."""
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        raise ParseError(f"Could not fetch {url}")

    text = trafilatura.extract(
        downloaded,
        include_tables=True,
        include_links=False,
        favor_precision=True,
    )
    if not text or not text.strip():
        raise ParseError(
            "No readable article content found at that URL. It may require "
            "JavaScript or be behind a login."
        )

    extracted_title = title
    if not extracted_title:
        metadata = trafilatura.extract_metadata(downloaded)
        extracted_title = (metadata.title if metadata else None) or url

    return ParsedDocument(
        title=extracted_title,
        blocks=split_into_sections(text),
        meta={"parser": "trafilatura", "url": url, "characters": len(text)},
    )
