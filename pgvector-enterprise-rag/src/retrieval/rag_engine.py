"""
retrieval/rag_engine.py
Core RAG pipeline: embed query → vector search → LLM answer generation.
Author: Suresh Nadipineni — Senior DBA & AI Data Infra Engineer
"""

import os
import time
import logging
from typing import Optional

from openai import AsyncOpenAI
from ..db.vector_ops import search_similar, SimilarChunk
from ..db.connection import get_connection

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EMBED_MODEL  = "text-embedding-3-small"   # 1536 dims, cost-efficient
CHAT_MODEL   = os.getenv("LLM_MODEL", "gpt-4o-mini")
MAX_CONTEXT  = int(os.getenv("RAG_MAX_CONTEXT_CHARS", 6000))
TOP_K        = int(os.getenv("RAG_TOP_K", 5))
MIN_SIM      = float(os.getenv("RAG_MIN_SIMILARITY", 0.70))

SYSTEM_PROMPT = """You are an enterprise AI assistant with access to company documents.
Answer questions using ONLY the provided context. Be concise and factual.
If the context does not contain the answer, say: "I don't have that information in the knowledge base."
Always cite the source document(s) you used."""


async def embed_query(text: str) -> list[float]:
    """Convert a text query into a 1536-dim embedding vector."""
    response = await client.embeddings.create(
        model=EMBED_MODEL,
        input=text.strip(),
    )
    return response.data[0].embedding


def build_context(chunks: list[SimilarChunk]) -> str:
    """
    Assemble retrieved chunks into a context string for the LLM.
    Respects MAX_CONTEXT char limit.
    """
    context_parts = []
    total_chars = 0

    for chunk in chunks:
        header = f"\n[Source: {chunk.document_title} | Similarity: {chunk.similarity:.0%}]\n"
        entry = header + chunk.content
        if total_chars + len(entry) > MAX_CONTEXT:
            break
        context_parts.append(entry)
        total_chars += len(entry)

    return "\n---\n".join(context_parts)


async def log_query(
    question: str,
    answer: str,
    chunks: list[SimilarChunk],
    latency_ms: int,
    user_id: Optional[str] = None,
) -> None:
    """Write query + result to audit log table (SOX/HIPAA compliance)."""
    try:
        from ..db.connection import get_connection
        async with get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO query_log
                    (user_id, question, answer, source_chunk_ids, similarity_scores, model_used, latency_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                user_id,
                question,
                answer,
                [c.chunk_id for c in chunks],
                [c.similarity for c in chunks],
                CHAT_MODEL,
                latency_ms,
            )
    except Exception as exc:
        logger.warning("Failed to log query: %s", exc)


async def ask(
    question: str,
    department: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """
    Full RAG pipeline:
    1. Embed the question
    2. Search pgvector for similar chunks
    3. Build context from retrieved chunks
    4. Generate LLM answer grounded in context
    5. Log to audit table
    Returns: { answer, sources, latency_ms }
    """
    start = time.perf_counter()

    # Step 1 — Embed query
    query_vec = await embed_query(question)

    # Step 2 — Vector similarity search in PostgreSQL
    similar_chunks = await search_similar(
        query_embedding=query_vec,
        top_k=TOP_K,
        min_similarity=MIN_SIM,
        department=department,
    )

    if not similar_chunks:
        return {
            "answer": "I don't have relevant information in the knowledge base for this question.",
            "sources": [],
            "latency_ms": int((time.perf_counter() - start) * 1000),
        }

    # Step 3 — Build context
    context = build_context(similar_chunks)

    # Step 4 — LLM generation
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}",
        },
    ]

    response = await client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.1,      # low temp for factual grounding
        max_tokens=512,
    )

    answer = response.choices[0].message.content.strip()
    latency_ms = int((time.perf_counter() - start) * 1000)

    # Step 5 — Audit log
    await log_query(question, answer, similar_chunks, latency_ms, user_id)

    logger.info(
        "RAG query completed: latency=%dms chunks_retrieved=%d",
        latency_ms, len(similar_chunks),
    )

    return {
        "answer": answer,
        "sources": [
            {
                "document": c.document_title,
                "similarity": c.similarity,
                "excerpt": c.content[:200] + "..." if len(c.content) > 200 else c.content,
            }
            for c in similar_chunks
        ],
        "latency_ms": latency_ms,
    }
