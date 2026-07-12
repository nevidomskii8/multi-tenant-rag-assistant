# Devlog

Session-by-session notes. Newest first. See `docs/roadmap.md` for the full plan
and `CLAUDE.md` for current status.

## 2026-07-12 — Phase 1: single-tenant RAG core (DONE)

**Done**
- Schema via Alembic: `documents`, `chunks(embedding vector(384))`, HNSW cosine
  index (`migrations/versions/0001_*`). `env.py` reuses `app.config` for the DB
  URL (one source of truth, no secrets in `alembic.ini`).
- Ingestion (`app/ingest.py`): `data/kb/*.md` → token-based `chunk_text`
  (windows measured with the model tokenizer + boundary check, so Cyrillic can't
  silently overflow the 512-token limit) → `embed_passages` (e5 `passage:`) →
  pgvector. Idempotent per `source` (now `path.as_posix()`).
- Embeddings (`app/embeddings.py`): `multilingual-e5-small`, lazy-loaded once
  (`lru_cache`), `passage:`/`query:` prefixes, tokenizer/`max_seq_length` helpers.
- Retrieval (`app/retrieval.py`): `query:` embed → pgvector `<=>` cosine top-k.
- Chat (`app/rag.py`, `app/main.py`): `POST /chat` → retrieve → context in
  `<context>` tags (data, not instructions) → one Claude call → `{answer,
  sources}`. `answer()` also returns the exact `context` (for eval); `/chat`
  keeps it internal. Input length + `k` + `max_tokens` capped.
- Eval (`eval/`): 8-item `golden.json`; `gate.py` = deterministic top-1
  retrieval gate wired into CI (`eval-gate` job: pgvector service + migrate +
  ingest, no API key); `run.py` = local LLM-judge faithfulness/relevancy + fact
  + refusal checks.

**Verified (DoD)**
- `alembic upgrade head` → schema present (`\d chunks` shows `vector(384)` + hnsw).
- Ingest: 3 docs / 3 chunks. Retrieval: correct top-1 on manual queries.
- `/chat`: accurate grounded answer with sources; out-of-scope → refusal.
- `python -m eval.gate` → 6/6 top-1, GATE PASSED (local). CI `eval-gate` green.
- `ruff check` / `ruff format --check` / `pytest` green.

**Decisions / notes**
- LLM-judge eval NOT in CI (cost + non-determinism + secret exposure); CI gates
  on the deterministic retrieval check. LLM judge runs locally by hand. See
  ADRs and the eval/gate vs eval/run split.
- Applied code-review fixes: judge grades the exact model context (not a
  re-retrieval); judge failures are per-item, not fatal; stable `source` key.
- Deferred to `docs/backlog.md`: chat UI (step 5), DeepEval, HF cache volume,
  smarter chunker, judge-based refusal, separate judge model, httpx2.

**Next**
- Phase 2 — multi-tenancy + Postgres Row-Level Security (LLM08/LLM02).

## 2026-07-10 — Phase 0: scaffolding (DONE)

**Done**
- Repo structure: `app/` (FastAPI), `tests/`, `nginx/`, `db/`.
- `docker-compose.yml`: `db` (pgvector/pgvector:pg16, healthcheck, `init.sql`
  enables the `vector` extension) → `app` (FastAPI, depends on db healthy) →
  `nginx` (reverse proxy, published on `:8080`).
- App: `/health` endpoint; config via `pydantic-settings` (`app/config.py`).
- `Dockerfile` (runtime-only deps), `pyproject.toml` (deps + ruff/pytest config),
  `.env.example`, `.dockerignore`.
- CI (`.github/workflows/ci.yml`): ruff lint + ruff format check + pytest +
  gitleaks. Local pre-commit: ruff + gitleaks.

**Verified (DoD)**
- `docker compose up --build -d` → `curl localhost:8080/health` →
  `{"status":"ok","env":"dev"}`.
- Local gates green: `ruff check`, `ruff format --check`, `pytest` (1 passed).
- Pushed to GitHub; CI green.

**Decisions / notes**
- Tooling defaults: ruff (lint+format), pytest, pydantic-settings — conventional,
  no scope expansion.
- Known non-blocking warning: Starlette `TestClient` deprecates `httpx` in favour
  of `httpx2`. Deferred (backlog); does not fail CI.
- Sanitized public repo: internal infra specifics moved to gitignored
  `docs/infra.local.md`; `CLAUDE.md` stays generic.

**Next**
- Phase 1 — RAG core, single-tenant (see plan below / roadmap §Phase 1).