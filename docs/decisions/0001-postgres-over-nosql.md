# 0001 — Postgres (with pgvector) over NoSQL

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

The app stores conversations, uploaded documents with their embeddings, and
workflow definitions plus run traces. A document store (MongoDB) and a
relational store (Postgres) were both plausible.

## Decision

Use **Postgres 17 + pgvector** as the single datastore, with `JSONB` columns for
the genuinely schemaless parts.

## Reasoning

1. The data is relational almost everywhere: `user → conversation → message`,
   `notebook → document → chunk`, `workflow → version → run → step`. Nearly
   every query is a join or a traversal down one of those chains.
2. Local MongoDB has no vector search (Atlas Vector Search is cloud-only), so
   the NoSQL path forces a second system (Qdrant/Chroma) — two things to run,
   two to back up, and a dual-write consistency problem between a chunk row and
   its embedding.
3. Hybrid retrieval (M4) fuses keyword and vector ranking. Postgres does
   `tsvector` and vector cosine in one SQL statement; Mongo + Qdrant needs two
   round-trips fused in Python.
4. Ingestion and workflow runs need real transactions — one document plus N
   chunks must commit or fail as a unit, or half-ingested documents silently
   poison retrieval.
5. RAM: Postgres idles at ~50–100 MB. Mongo plus a vector DB costs much more of
   a 16 GB budget already dominated by the model.

## Consequences

- Schema changes require Alembic migrations (a discipline, not a drawback).
- Document flexibility comes from `JSONB` + GIN indexes: `workflow_versions.graph`,
  `workflow_run_steps.input/output`, `messages.metadata`, `documents.source_meta`.
- If vector volume ever outgrows pgvector (millions of chunks), a dedicated
  vector DB can be introduced behind `rag/store.py` without touching callers.
