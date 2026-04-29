"""
db/connection.py
PostgreSQL connection pool for pgvector RAG system.
Production-ready with pgBouncer support.
Author: Suresh Nadipineni — Senior DBA & AI Data Infra Engineer
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Connection config ────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "ragdb"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "min_size": int(os.getenv("DB_POOL_MIN", 2)),
    "max_size": int(os.getenv("DB_POOL_MAX", 20)),
    # statement_cache_size=0 required when using pgBouncer in transaction mode
    "statement_cache_size": int(os.getenv("DB_STATEMENT_CACHE", 0)),
}

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the global connection pool, creating it if necessary."""
    global _pool
    if _pool is None:
        logger.info(
            "Creating DB pool: %s@%s:%s/%s (min=%s max=%s)",
            DB_CONFIG["user"], DB_CONFIG["host"], DB_CONFIG["port"],
            DB_CONFIG["database"], DB_CONFIG["min_size"], DB_CONFIG["max_size"],
        )
        _pool = await asyncpg.create_pool(**DB_CONFIG)
        # Register the pgvector codec so asyncpg understands the vector type
        await _register_vector_codec(_pool)
        logger.info("DB pool created successfully.")
    return _pool


async def _register_vector_codec(pool: asyncpg.Pool) -> None:
    """Register pgvector type codec with asyncpg."""
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # Fetch the OID for the vector type
        vector_oid = await conn.fetchval(
            "SELECT oid FROM pg_type WHERE typname = 'vector'"
        )
        if vector_oid:
            # Register encoder/decoder for the vector type
            pool.set_codec(
                "vector",
                schema="public",
                encoder=lambda v: f"[{','.join(str(x) for x in v)}]",
                decoder=lambda s: [float(x) for x in s.strip("[]").split(",")],
                format="text",
            )
            logger.info("pgvector codec registered (OID: %s)", vector_oid)


async def close_pool() -> None:
    """Gracefully close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("DB pool closed.")


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Async context manager for a single DB connection from the pool.

    Usage:
        async with get_connection() as conn:
            result = await conn.fetch("SELECT ...")
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def get_transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    """
    Async context manager for a transactional DB connection.

    Usage:
        async with get_transaction() as conn:
            await conn.execute("INSERT ...")
            await conn.execute("UPDATE ...")
            # auto-commits on exit, rolls back on exception
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


async def health_check() -> dict:
    """Check DB connectivity and pgvector availability."""
    try:
        async with get_connection() as conn:
            version = await conn.fetchval("SELECT version()")
            vec_ext = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            row_count = await conn.fetchval(
                "SELECT COUNT(*) FROM document_embeddings"
            )
            return {
                "status": "healthy",
                "postgresql": version.split(" ")[1] if version else "unknown",
                "pgvector": vec_ext or "not installed",
                "embeddings_stored": row_count,
            }
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        return {"status": "unhealthy", "error": str(exc)}
