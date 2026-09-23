# 0005 — pymupdf4llm over docling for document parsing

- **Status:** Accepted
- **Date:** 2026-09-22
- **Amends:** the parser choice named in M3 of [docs/PLAN.md](../PLAN.md)

## Context

M3 needs to turn PDFs, Word documents, notes and web pages into text with
enough structure to support citations. The plan named `docling`, chosen before
we had measured how the machine behaves under load.

Since then two things became clear. The chat model holds 5–6 GB resident while
in use, and benchmarking several models in succession already pushed the machine
into ~4 GB of swap. docling's advantage — better tables and multi-column
layouts — comes from running layout-detection models, which means a PyTorch
install of roughly 2.5 GB and inference competing for memory with Ollama during
ingestion.

## Decision

Use **pymupdf4llm** (PyMuPDF) for PDFs, **python-docx** for Word, plain reading
for text and markdown, and **trafilatura** for URLs. Put all of them behind a
`Parser` protocol in `app/rag/parse.py`, matching the `LLMProvider` seam.

## Reasoning

- The whole backend virtualenv is 382 MB with no torch. docling would roughly
  triple that and add ML inference to every ingestion.
- pymupdf4llm returns markdown per page with page numbers, which is what
  citations actually need. Headings come through as ATX markdown, so sections
  are recoverable without a layout model.
- Measured: a 52-page PDF parses, chunks and embeds in about 5 seconds.
- The interface makes this reversible. Adding docling later is one class and one
  registry entry, with nothing downstream changing.

## Consequences

- Complex tables degrade to plain text. Acceptable for prose documents; a
  limitation for table-dense papers.
- **Scanned PDFs are not supported.** There is no OCR, so an image-only PDF
  fails with an explicit message rather than silently indexing nothing.
- If table quality becomes a real problem, add a `DoclingParser` behind the same
  protocol and select it per document rather than globally.
