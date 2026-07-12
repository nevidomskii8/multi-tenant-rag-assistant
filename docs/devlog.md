# Devlog

Session-by-session notes. Newest first. See `docs/roadmap.md` for the full plan
and `CLAUDE.md` for current status.

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