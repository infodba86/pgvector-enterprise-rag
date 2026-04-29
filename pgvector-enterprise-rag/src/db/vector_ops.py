"""
db/vector_ops.py
pgvector CRUD operations — insert, search, delete embeddings.
Author: Suresh Nadipineni — Senior DBA & AI Data Infra Engineer
"""

import logging
from typing import List, Optional
from dataclasses import dataclass

from .connection import get_connection, get_transaction

logger = logging.getLogger(__name__)


@dataclass
class SimilarChunk:
    chunk_id: int
    document_id: int
    document_title: str
    content: str
    similarity: float


async def insert_document(
    title: str,
    source_type: str,
    department: str,
    metadata: dict | None = None,
) -> int:
    """Insert a document record, return document_id."""
    async with get_transaction() as conn:
        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (title, source_type, department, metadata)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            title, source_type, department,
            str(metadata or {}).replace("'", '"'),
        )
    logger.info("Inserted document id=%s title='%s'", doc_id, title)
    return doc_id


async def insert_chunks_and_embeddings(
    document_id: int,
    chunks: List[str],
    embeddings: List[List[float]],
) -> List[int]:
    """
    Bulk insert chunks and their embeddings in a single transaction.
    Returns list of embedding IDs.
    """
    assert len(chunks) == len(embeddings), "chunks and embeddings must match"

    embedding_ids: List[int] = []

    async with get_transaction() as conn:
        for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            # Insert chunk
            chunk_id = await conn.fetchval(
                """
                INSERT INTO document_chunks (document_id, chunk_index, content, token_count)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                document_id, idx, chunk_text, len(chunk_text.split()),
            )

            # Insert embedding using pgvector format
            vec_str = f"[{','.join(str(round(x, 6)) for x in embedding)}]"
            emb_id = await conn.fetchval(
                """
                INSERT INTO document_embeddings (chunk_id, document_id, embedding)
                VALUES ($1, $2, $3::vector)
                RETURNING id
                """,
                chunk_id, document_id, vec_str,
            )
            embedding_ids.append(emb_id)

    logger.info(
        "Inserted %d chunks + embeddings for document_id=%s",
        len(chunks), document_id,
    )
    return embedding_ids


async def search_similar(
    query_embedding: List[float],
    top_k: int = 5,
    min_similarity: float = 0.70,
    department: Optional[str] = None,
) -> List[SimilarChunk]:
    """
    Cosine similarity search using pgvector <=> operator.
    Returns top_k most semantically similar chunks.

    The <=> operator = cosine distance (0 = identical, 2 = opposite).
    Similarity = 1 - cosine_distance.
    """
    vec_str = f"[{','.join(str(round(x, 6)) for x in query_embedding)}]"

    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM search_similar_chunks(
                $1::vector,
                $2,
                $3,
                $4
            )
            """,
            vec_str, top_k, min_similarity, department,
        )

    results = [
        SimilarChunk(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            content=row["chunk_content"],
            similarity=round(row["similarity"], 4),
        )
        for row in rows
    ]

    logger.debug(
        "Vector search returned %d results (top similarity: %.3f)",
        len(results),
        results[0].similarity if results else 0,
    )
    return results


async def delete_document(document_id: int) -> None:
    """Soft-delete a document (cascades to chunks and embeddings via FK)."""
    async with get_transaction() as conn:
        await conn.execute(
            "UPDATE documents SET is_active = FALSE WHERE id = $1",
            document_id,
        )
    logger.info("Soft-deleted document_id=%s", document_id)


async def get_embedding_stats() -> dict:
    """DBA monitoring — returns vector store statistics."""
    async with get_connection() as conn:
        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(DISTINCT d.id)  AS total_documents,
                COUNT(DISTINCT dc.id) AS total_chunks,
                COUNT(de.id)          AS total_embeddings,
                AVG(dc.token_count)   AS avg_chunk_tokens,
                MAX(de.created_at)    AS last_embedding_at
            FROM documents d
            LEFT JOIN document_chunks dc ON dc.document_id = d.id
            LEFT JOIN document_embeddings de ON de.document_id = d.id
            WHERE d.is_active = TRUE
            """
        )
        index_health = await conn.fetch("SELECT * FROM check_vector_index_health()")

    return {
        "documents": stats["total_documents"],
        "chunks": stats["total_chunks"],
        "embeddings": stats["total_embeddings"],
        "avg_chunk_tokens": round(stats["avg_chunk_tokens"] or 0, 1),
        "last_embedding_at": str(stats["last_embedding_at"]),
        "index_health": [dict(row) for row in index_health],
    }
