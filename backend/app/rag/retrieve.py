"""Hybrid retrieval: vector similarity and keyword ranking, fused with RRF.

Neither half is sufficient alone. Vector search finds passages that mean the
same thing in different words but misses exact identifiers — a part number, an
error code, a surname. Keyword search nails those and misses paraphrase.

Reciprocal Rank Fusion combines them by *rank* rather than score, which matters
because cosine similarity and ts_rank are on incomparable scales; normalising
them against each other would be arbitrary. RRF only asks "how near the top did
each method put this?".

The fusion runs in one SQL statement. Doing it in Python would mean two
round-trips and hand-rolled ranking over rows we already asked Postgres to
order (ADR 0001).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import get_registry

logger = logging.getLogger(__name__)

# The standard RRF damping constant. Larger values flatten the weighting
# between ranks; 60 is the value from the original paper and behaves well
# without tuning.
RRF_K = 60

# How deep to look in each method before fusing. Wider than the final limit so
# a passage ranked poorly by one method can still be rescued by the other.
CANDIDATE_DEPTH = 40

# Tokens Postgres would treat as tsquery operators, and anything else that is
# not a word character. Stripped rather than escaped: these come from a user's
# question, never from a query language.
_WORD = re.compile(r"[A-Za-z0-9_]+")


def to_or_tsquery(query: str) -> str | None:
    """Turn a question into an OR tsquery, or None if there is nothing to match.

    `plainto_tsquery` and `websearch_to_tsquery` both AND every term, so a
    natural-language question ("What pressure should the Kestrel Mark VII
    regulator be calibrated to, and when must it not be done?") matches almost
    nothing and the keyword half contributes nothing at all. OR semantics is
    what hybrid retrieval wants: find passages sharing any significant term,
    and let ts_rank_cd reward the ones sharing more.

    Terms are extracted rather than escaped, so no user input can reach the
    tsquery parser as syntax.
    """
    terms = [t.lower() for t in _WORD.findall(query) if len(t) > 1]
    if not terms:
        return None
    # Postgres's english config drops stopwords itself; a query of only
    # stopwords yields an empty tsquery, which matches nothing rather than
    # erroring.
    return " | ".join(dict.fromkeys(terms))


@dataclass(slots=True)
class Retrieved:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    content: str
    page: int | None
    end_page: int | None
    section: str | None
    start_char: int | None
    end_char: int | None
    score: float
    vector_rank: int | None
    keyword_rank: int | None

    @property
    def page_label(self) -> str | None:
        if self.page is None:
            return None
        if self.end_page is None or self.end_page == self.page:
            return f"p{self.page}"
        return f"p{self.page}–{self.end_page}"

    def cite(self) -> str:
        """Short human-readable source, e.g. 'Handbook.pdf, p31–32, Chapter 3'."""
        parts = [self.document_title]
        if self.page_label:
            parts.append(self.page_label)
        if self.section:
            parts.append(self.section)
        return ", ".join(parts)


_HYBRID_SQL = text(
    """
    WITH vector_hits AS (
        SELECT c.id,
               ROW_NUMBER() OVER (ORDER BY c.embedding <=> CAST(:embedding AS vector)) AS rank
        FROM chunks c
        WHERE c.notebook_id = :notebook_id
          AND (CAST(:document_ids AS uuid[]) IS NULL
               OR c.document_id = ANY(CAST(:document_ids AS uuid[])))
        ORDER BY c.embedding <=> CAST(:embedding AS vector)
        LIMIT :depth
    ),
    keyword_hits AS (
        SELECT c.id,
               ROW_NUMBER() OVER (
                   ORDER BY ts_rank_cd(c.content_tsv, query) DESC
               ) AS rank
        FROM chunks c, to_tsquery('english', :query_text) AS query
        WHERE c.notebook_id = :notebook_id
          AND (CAST(:document_ids AS uuid[]) IS NULL
               OR c.document_id = ANY(CAST(:document_ids AS uuid[])))
          AND c.content_tsv @@ query
        ORDER BY ts_rank_cd(c.content_tsv, query) DESC
        LIMIT :depth
    )
    SELECT c.id            AS chunk_id,
           c.document_id   AS document_id,
           d.title         AS document_title,
           c.content       AS content,
           c.page          AS page,
           c.end_page      AS end_page,
           c.section       AS section,
           c.start_char    AS start_char,
           c.end_char      AS end_char,
           v.rank          AS vector_rank,
           k.rank          AS keyword_rank,
           COALESCE(1.0 / (:rrf_k + v.rank), 0.0)
             + COALESCE(1.0 / (:rrf_k + k.rank), 0.0) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    LEFT JOIN vector_hits  v ON v.id = c.id
    LEFT JOIN keyword_hits k ON k.id = c.id
    WHERE v.id IS NOT NULL OR k.id IS NOT NULL
    ORDER BY score DESC, c.position ASC
    LIMIT :limit
    """
)


async def hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    notebook_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
    limit: int = 8,
) -> list[Retrieved]:
    """Retrieve passages for a question, best first.

    `document_ids` restricts the search to a subset, which is how the UI's
    per-source include/exclude toggles work. An empty list means every source
    was excluded, so there is nothing to search.
    """
    if document_ids is not None and not document_ids:
        return []

    embedding = await get_registry().embed_query(query)

    # No usable terms means the keyword half simply finds nothing; the CTE
    # still runs and contributes no ranks, leaving pure vector search.
    keyword_query = to_or_tsquery(query) or ""

    rows = (
        await session.execute(
            _HYBRID_SQL,
            {
                # pgvector accepts its literal text form; psycopg would need an
                # adapter registered for a bare list.
                "embedding": "[" + ",".join(str(v) for v in embedding) + "]",
                "query_text": keyword_query,
                "notebook_id": notebook_id,
                "document_ids": [str(d) for d in document_ids] if document_ids else None,
                "depth": CANDIDATE_DEPTH,
                "rrf_k": RRF_K,
                "limit": limit,
            },
        )
    ).mappings()

    return [
        Retrieved(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            content=row["content"],
            page=row["page"],
            end_page=row["end_page"],
            section=row["section"],
            start_char=row["start_char"],
            end_char=row["end_char"],
            score=float(row["score"]),
            vector_rank=row["vector_rank"],
            keyword_rank=row["keyword_rank"],
        )
        for row in rows
    ]
