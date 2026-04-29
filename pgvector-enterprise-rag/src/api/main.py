"""
api/main.py
FastAPI endpoints for the pgvector Enterprise RAG system.
Author: Suresh Nadipineni — Senior DBA & AI Data Infra Engineer
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from ..db.connection import get_pool, close_pool, health_check
from ..db.vector_ops import get_embedding_stats
from ..retrieval.rag_engine import ask
from ..ingestion.embedder import ingest_document

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan: init/close DB pool ────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initialising DB pool...")
    await get_pool()
    yield
    logger.info("Shutting down — closing DB pool...")
    await close_pool()


app = FastAPI(
    title="pgvector Enterprise RAG",
    description="PostgreSQL-powered semantic search and RAG API",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Request/Response models ──────────────────────────────────
class QueryRequest(BaseModel):
    question: str
    department: Optional[str] = None
    user_id: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
    sources: list
    latency_ms: int


class IngestRequest(BaseModel):
    title: str
    content: str
    source_type: str = "txt"
    department: str = "shared"
    metadata: dict = {}


# ── Endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    """DB connectivity and pgvector status check."""
    return await health_check()


@app.get("/stats")
async def stats():
    """Vector store statistics — documents, chunks, embeddings, index health."""
    return await get_embedding_stats()


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """
    Main RAG endpoint.
    Embeds the question, searches pgvector, generates grounded LLM answer.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    result = await ask(
        question=req.question,
        department=req.department,
        user_id=req.user_id,
    )
    return QueryResponse(**result)


@app.post("/ingest")
async def ingest(req: IngestRequest):
    """
    Ingest a document: chunk it, generate embeddings, store in PostgreSQL.
    """
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")

    doc_id = await ingest_document(
        title=req.title,
        content=req.content,
        source_type=req.source_type,
        department=req.department,
        metadata=req.metadata,
    )
    return {"document_id": doc_id, "status": "ingested"}


@app.delete("/documents/{document_id}")
async def delete_document_endpoint(document_id: int):
    """Soft-delete a document and its embeddings."""
    from ..db.vector_ops import delete_document
    await delete_document(document_id)
    return {"document_id": document_id, "status": "deleted"}
