# Architecture Decision Records

Newest first. Format: context → decision → consequences.

## ADR-004 — Room-based tenancy enforced by RLS via a non-owner role (2026-07-16)

**Context:** Phase 2 is the security core of B: isolate data between tenants. The
maintainer's model is a **room** — a shareable RAG space with its own data that
multiple users access via **membership** (many-to-many) — rather than a rigid
"tenant = company, one user → one tenant". The isolation must be provable, not
just present (OWASP LLM08 vector/embedding leakage, LLM02 sensitive info,
broken access control).

**Decision:**
- **Room model.** `users` + `rooms` + `memberships` (M:N). Flat membership: any
  member reads/queries *and loads data into* a room; the creator (`owner`) adds
  members. RBAC (owner/editor/viewer) and an org umbrella are deferred.
- **RLS via a non-owner runtime role.** A superuser *or the table owner* bypasses
  RLS, and the pgvector image makes `app` a superuser+owner. So the request path
  runs as a separate **`app_rt`** role (non-superuser, non-owner, DML grants
  only); `app` is used only for migrations + operator seed. Per-request identity
  is a transaction-local `app.user_id` (`set_config(..., is_local=true)`), read by
  policies as `NULLIF(current_setting('app.user_id', true), '')::int` —
  **deny-by-default** (unset/empty → NULL → no rows).
- **Reads and writes both gated.** Membership `USING` for reads; `WITH CHECK` for
  writes, so a member can only load data into their own rooms. This is what makes
  the user upload path (`POST /rooms/{id}/documents`) safe; email/agent writers
  reuse it in Phase C.
- **Chunk↔room integrity by constraint, not code.** `chunks.room_id` is
  denormalized (so RLS filters without a join); a **composite FK**
  `(document_id, room_id) → documents(id, room_id)` guarantees a chunk's room
  matches its parent document. FK checks bypass RLS, so this is the real guard.
- **404, not 403, for non-member rooms.** A forbidden room is indistinguishable
  from a non-existent one, so membership can't be enumerated. (`/rooms/{id}/members`
  returns 403 to a non-owner *member*, who already knows the room exists.)

**Consequences:**
- Isolation is enforced at the database, independent of app correctness, and is
  pinned by DB-layer + API-layer regression tests in CI (`tests/test_isolation.py`).
- `app_rt`'s password is created in migration `0002` from `APP_RT_PASSWORD` (env),
  never hardcoded — the repo is public and gitleaks would flag a literal.
- **HNSW + `room_id` filter caveat:** a filtered ANN scan can return < k rows
  (post-filtering). Fine at this scale and the eval gate would catch a regression;
  pgvector ≥ 0.8 iterative index scans are the escape hatch if it ever bites.
- The request path must never be in autocommit (`SET LOCAL` no-ops outside a tx);
  the deny-by-default test catches a regression here.
- **No `INSERT ... ON CONFLICT` on RLS tables where the row isn't SELECT-visible
  to the writer.** ON CONFLICT must read the conflicting row, so it needs the row
  to pass the SELECT policy; an owner adding *another* user's membership (which
  `memberships_select` hides) makes ON CONFLICT raise an RLS error. Use a plain
  INSERT and treat the UniqueViolation as idempotent success at the API layer.

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