-- Enable pgvector. Real schema (tenants, documents, embeddings, RLS) lands in Phase 1–2.
CREATE EXTENSION IF NOT EXISTS vector;
