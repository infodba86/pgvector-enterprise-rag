"""
ingestion/embedder.py
Document ingestion pipeline: chunk → embed → store in pgvector.
Author: Suresh Nadipineni — Senior DBA & AI Data Infra Engineer
"""

import os
import logging
from typing import List

from openai import AsyncOpenAI
from ..db.vector_ops import insert_document, insert_chunks_and_embeddings

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EMBED_MODEL  = "text-embedding-3-small"
CHUNK_SIZE   = int(os.getenv("CHUNK_SIZE", 500))      # tokens approx
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 50))   # overlap between chunks
BATCH_SIZE   = int(os.getenv("EMBED_BATCH_SIZE", 50)) # OpenAI batch limit


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Split text into overlapping chunks by word count.
    Overlap ensures context is not lost at chunk boundaries.
    """
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk.strip())
        start += chunk_size - overlap   # slide with overlap

    return [c for c in chunks if len(c.strip()) > 20]  # skip tiny chunks


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings for a list of texts using OpenAI API.
    Batches requests to respect API limits.
    """
    all_embeddings = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        logger.info("Embedding batch %d-%d of %d", i, i + len(batch), len(texts))

        response = await client.embeddings.create(
            model=EMBED_MODEL,
            input=batch,
        )
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings


async def ingest_document(
    title: str,
    content: str,
    source_type: str = "txt",
    department: str = "shared",
    metadata: dict | None = None,
) -> int:
    """
    Full ingestion pipeline:
    1. Chunk the document
    2. Generate embeddings via OpenAI
    3. Store document + chunks + embeddings in PostgreSQL (pgvector)
    Returns: document_id
    """
    logger.info("Ingesting document: '%s' (%d chars)", title, len(content))

    # Step 1 — Chunk
    chunks = chunk_text(content)
    logger.info("Split into %d chunks", len(chunks))

    # Step 2 — Embed
    embeddings = await embed_texts(chunks)

    # Step 3 — Store
    doc_id = await insert_document(title, source_type, department, metadata)
    await insert_chunks_and_embeddings(doc_id, chunks, embeddings)

    logger.info(
        "Ingestion complete: doc_id=%s title='%s' chunks=%d",
        doc_id, title, len(chunks),
    )
    return doc_id
