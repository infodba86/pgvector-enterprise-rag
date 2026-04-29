-- ============================================================
-- pgvector Enterprise RAG Schema
-- Author: Suresh Nadipineni — Senior DBA & AI Data Infra Engineer
-- Description: Production-grade vector database schema for RAG systems
-- PostgreSQL 16 + pgvector extension
-- ============================================================

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- for hybrid search

-- ── DOCUMENT STORE ──────────────────────────────────────────
-- Stores original documents before chunking
CREATE TABLE IF NOT EXISTS documents (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    source_path     TEXT,
    source_type     TEXT CHECK (source_type IN ('pdf','txt','html','docx','markdown')),
    department      TEXT,                          -- for RLS filtering
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'::jsonb,     -- flexible metadata store
    is_active       BOOLEAN DEFAULT TRUE
);

-- ── DOCUMENT CHUNKS ─────────────────────────────────────────
-- Each document is split into chunks for embedding
CREATE TABLE IF NOT EXISTS document_chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,              -- order within document
    content         TEXT NOT NULL,                 -- raw text of this chunk
    token_count     INTEGER,                       -- approximate token count
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── EMBEDDINGS (VECTOR STORE) ────────────────────────────────
-- Core pgvector table — stores 1536-dim OpenAI embeddings
CREATE TABLE IF NOT EXISTS document_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    chunk_id        BIGINT REFERENCES document_chunks(id) ON DELETE CASCADE,
    document_id     BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    embedding       vector(1536) NOT NULL,         -- OpenAI text-embedding-3-small
    model_name      TEXT DEFAULT 'text-embedding-3-small',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── QUERY LOG ────────────────────────────────────────────────
-- Audit trail for all RAG queries (SOX/HIPAA compliance)
CREATE TABLE IF NOT EXISTS query_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         TEXT,
    question        TEXT NOT NULL,
    answer          TEXT,
    source_chunk_ids BIGINT[],
    similarity_scores FLOAT[],
    model_used      TEXT,
    latency_ms      INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- INDEXES — Critical for performance at scale
-- ============================================================

-- Standard B-tree indexes
CREATE INDEX IF NOT EXISTS idx_documents_department
    ON documents(department) WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_chunks_document_id
    ON document_chunks(document_id);

CREATE INDEX IF NOT EXISTS idx_embeddings_document_id
    ON document_embeddings(document_id);

-- pgvector IVFFlat index for cosine similarity search
-- lists = sqrt(num_rows) is a good starting point
-- For < 1M vectors: ivfflat; For > 1M vectors: switch to hnsw
CREATE INDEX IF NOT EXISTS idx_embeddings_vector_cosine
    ON document_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- HNSW index (uncomment for > 1M vectors — better recall)
-- CREATE INDEX IF NOT EXISTS idx_embeddings_vector_hnsw
--     ON document_embeddings
--     USING hnsw (embedding vector_cosine_ops)
--     WITH (m = 16, ef_construction = 64);

-- GIN index for hybrid keyword+vector search
CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm
    ON document_chunks USING gin(content gin_trgm_ops);

-- JSONB index for metadata filtering
CREATE INDEX IF NOT EXISTS idx_documents_metadata
    ON documents USING gin(metadata);

-- ============================================================
-- ROW-LEVEL SECURITY (RLS) — HIPAA/SOX compliance
-- ============================================================

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_embeddings ENABLE ROW LEVEL SECURITY;

-- Users can only see documents from their own department
-- (or documents marked as public/shared)
CREATE POLICY dept_isolation ON documents
    FOR SELECT
    USING (
        department = current_setting('app.current_user_dept', TRUE)
        OR department = 'shared'
        OR current_setting('app.is_admin', TRUE) = 'true'
    );

CREATE POLICY embedding_isolation ON document_embeddings
    FOR SELECT
    USING (
        document_id IN (
            SELECT id FROM documents
            WHERE department = current_setting('app.current_user_dept', TRUE)
               OR department = 'shared'
        )
    );

-- ============================================================
-- HELPER FUNCTIONS
-- ============================================================

-- Top-K semantic similarity search
-- Returns most similar chunks with their source documents
CREATE OR REPLACE FUNCTION search_similar_chunks(
    query_embedding vector(1536),
    top_k           INTEGER DEFAULT 5,
    min_similarity  FLOAT DEFAULT 0.7,
    dept_filter     TEXT DEFAULT NULL
)
RETURNS TABLE (
    chunk_id        BIGINT,
    document_id     BIGINT,
    document_title  TEXT,
    chunk_content   TEXT,
    similarity      FLOAT
)
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id AS chunk_id,
        d.id  AS document_id,
        d.title AS document_title,
        dc.content AS chunk_content,
        1 - (de.embedding <=> query_embedding) AS similarity
    FROM document_embeddings de
    JOIN document_chunks dc ON dc.id = de.chunk_id
    JOIN documents d ON d.id = de.document_id
    WHERE
        d.is_active = TRUE
        AND (dept_filter IS NULL OR d.department = dept_filter OR d.department = 'shared')
        AND 1 - (de.embedding <=> query_embedding) >= min_similarity
    ORDER BY de.embedding <=> query_embedding   -- cosine distance (lower = more similar)
    LIMIT top_k;
END;
$$;

-- Embedding index health check (DBA monitoring)
CREATE OR REPLACE FUNCTION check_vector_index_health()
RETURNS TABLE (
    table_name      TEXT,
    index_name      TEXT,
    index_scans     BIGINT,
    index_type      TEXT,
    row_count       BIGINT
)
LANGUAGE sql AS $$
    SELECT
        t.relname::TEXT AS table_name,
        i.relname::TEXT AS index_name,
        s.idx_scan AS index_scans,
        am.amname::TEXT AS index_type,
        t.reltuples::BIGINT AS row_count
    FROM pg_stat_user_indexes s
    JOIN pg_class i ON i.oid = s.indexrelid
    JOIN pg_class t ON t.oid = s.relid
    JOIN pg_index ix ON ix.indexrelid = s.indexrelid
    JOIN pg_am am ON am.oid = i.relam
    WHERE t.relname IN ('document_embeddings', 'document_chunks', 'documents')
    ORDER BY t.relname, i.relname;
$$;

-- ============================================================
-- AUTOVACUUM TUNING for vector tables
-- High-write tables need aggressive autovacuum settings
-- ============================================================

ALTER TABLE document_embeddings SET (
    autovacuum_vacuum_scale_factor = 0.01,   -- vacuum after 1% of rows change
    autovacuum_analyze_scale_factor = 0.005, -- analyze after 0.5% change
    autovacuum_vacuum_cost_delay = 2         -- ms delay between vacuum pages
);

ALTER TABLE document_chunks SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01
);

-- ============================================================
-- SAMPLE DATA (for testing)
-- ============================================================

INSERT INTO documents (title, source_type, department, metadata) VALUES
    ('IT Security Policy v2.3', 'pdf', 'IT', '{"version": "2.3", "classification": "internal"}'),
    ('Database Backup & Recovery Runbook', 'markdown', 'shared', '{"author": "DBA Team", "last_review": "2025-01"}'),
    ('Employee Onboarding Guide', 'pdf', 'HR', '{"year": 2025}')
ON CONFLICT DO NOTHING;
