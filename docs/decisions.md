# Architecture Decision Records

Newest first. Format: context → decision → consequences.

## ADR-003 — Claude Haiku 4.5 for dev inference (2026-07-11)

**Context:** Phase 1 needs an inference model. The project explicitly tracks
denial-of-wallet risk (OWASP LLM10) and dev iterations are frequent.

**Decision:** `claude-haiku-4-5` as the dev default ($1/$5 per MTok vs $5/$25
for Opus 4.8). Model is a config value (`CLAUDE_MODEL`), not hardcoded.

**Consequences:** cheap iteration; answer quality is Haiku-tier. Switch to
`claude-opus-4-8` for prod/demo via env var — no code change.

## ADR-002 — Local embeddings: multilingual-e5-small (2026-07-11)

**Context:** Claude API has no embeddings endpoint — vectorization needs a
separate solution. KB docs will include en/ru/uk (maintainer's languages).
Candidates: Voyage AI (API), bge-small-en (local, en-only), BGE-M3 (local,
2.2GB, 8K context, hybrid dense+sparse), multilingual-e5-small (local, 450MB).

**Decision:** `intfloat/multilingual-e5-small` via sentence-transformers,
running locally on CPU. 384 dims, 100+ languages.

**Consequences:**
- No external embedding API → zero cost, no extra vendor, smaller
  denial-of-wallet surface (LLM10).
- +~1.5GB in the Docker image (torch CPU) — accepted.
- e5 models require `query: ` / `passage: ` prefixes at encode time.
- Swap path is cheap (config change + re-ingest); BGE-M3 / hybrid search noted
  as a future upgrade for the personal-assistant use case (backlog).
- Note: the "Claude API over a local model" decision concerned the LLM
  (RAM/GPU); an 80–450MB CPU embedder does not violate it.

## ADR-001 — HNSW index in pgvector (2026-07-11)

**Context:** pgvector offers HNSW and IVFFlat for approximate nearest-neighbor
search.

**Decision:** HNSW with `vector_cosine_ops`.

**Consequences:** better recall/latency at our scale, no training step
(IVFFlat needs data present before index build); slightly slower writes —
irrelevant at our volume.