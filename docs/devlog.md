# Devlog

Session-by-session notes. Newest first. See `docs/roadmap.md` for the full plan
and `CLAUDE.md` for current status.

## 2026-07-23 — Phase 3: live verification on the dev stack + findings

Phase 3 code exists in the working tree (uncommitted on `main`); this session
brought the full stack up (`docker compose up` incl. the `llm-guard-api` sidecar)
and exercised the guardrail path end-to-end against real scanner models — the
layer the mocked `tests/test_guardrails.py` can't cover.

**Verified live (room 3, seeded synthetic PII: 3 tickets/orders/profiles → 9 chunks)**
- **Sidecar scans for real:** `input_scan` audited as `allow` with `Anonymize` +
  `PromptInjection` both running (DeBERTa + Presidio loaded).
- **LLM02 output guard:** a "what is the customer email…" question → `Sensitive`
  scored 1.0 → block → fixed refusal, `sources` emptied. PII did **not** leak.
- **Fail-closed:** with `llm-guard-api` stopped, `/chat` returns 400 and logs
  `guardrail /analyze/prompt unavailable, failing closed: ConnectError` — a down
  guard denies, never falls back to raw model text.
- **Audit trail:** rows are room-scoped and `detail` holds only scanner
  names/scores + reason — no raw PII.

**Findings to fix before closing Phase 3**
1. **`data/` isn't mounted into the `app` container** (compose mounts only
   `./app`; the image COPYs only `app/`,`alembic.ini`,`migrations/`). So the
   operator seed `python -m app.pii` — and KB ingest — can't run in-container as
   their docstrings claim (`FileNotFoundError: data/pii/tickets.json`). Worked
   around with `docker compose cp ./data app:/app/data`; real fix is adding
   `- ./data:/app/data` to the app service volumes.
2. **refuse-vs-redact decision is implicit.** On a `Sensitive` block,
   `rag.answer` discards the sidecar's `sanitized_output` and returns a flat
   refusal, even though `guardrails/scanners.yml` sets `redact: true`. So the
   redacted answer is never used and the assistant refuses *any* answer that
   contains PII (strong LLM02, low utility for a support agent). Pick one
   behaviour deliberately and record it in ADR-005; consider returning the
   redacted text on a `Sensitive` block and reserving the flat refusal for the
   LLM07 canary.
3. **Stale `.env` / volume drift.** `.env` predates Phase 2/3 — missing
   `APP_RT_PASSWORD`, `RUNTIME_DATABASE_URL`, `JWT_SECRET`, `GUARDRAILS_*`; the
   stack runs on compose defaults. The persisted `pgdata` volume also carried an
   older `app`/`app_rt` password than `.env`, so migration/login failed until the
   role passwords were realigned (`ALTER USER … PASSWORD`). Regenerate `.env` from
   `.env.example`; `JWT_SECRET` should be ≥32 bytes (`openssl rand -hex 32`) — the
   app logged `InsecureKeyLengthWarning` on the 27-byte default.

**Also still open (process):** Phase 3 code is uncommitted on `main` and the docs
below still label the phase PLANNED — move to a `feat/phase3-guardrails` branch +
PR and flip status to DONE once the three findings are addressed.

## 2026-07-21 — Phase 3: guardrails & hardening (PLANNED)

Plan agreed; not yet built. OWASP focus: **LLM02** (PII disclosure), **LLM07**
(system-prompt leakage); reinforces **LLM01**. Architecture recorded in **ADR-005**
(llm-guard sidecar · Postgres `audit_log` under RLS · PII embedded into RAG).

**Workstreams / build order**
1. **PII data model (`migrations/0004`)** — room-scoped `tickets`, `orders`,
   `customer_profiles` + an `audit_log` table, all under the Phase 2 RLS + `app_rt`
   pattern; synthetic PII seed. *(Independent of #2 — can run in parallel.)*
2. **Guardrail sidecar** — `llm-guard-api` service in `docker-compose.yml` (pinned,
   healthchecked); URL wired into `app/config.py`; input scanners (prompt-injection,
   PII anonymize, token/topic caps) + output scanners (sensitive/PII, system-prompt-
   leak guard).
3. **Ingest PII into RAG** — render records → chunk → e5 `passage:` embed →
   `chunks` with `room_id`; retrieval unchanged (RLS already scopes it).
4. **Wire guardrails** — `app/guardrails.py` (`scan_input`/`scan_output`, fail-closed);
   `scan_input` in `/chat` before retrieval, `scan_output` in `rag.answer` after the
   Claude call; blocked → structured refusal, never raw model text. Harden `SYSTEM`
   for LLM07 (non-disclosure instruction + canary the output guard bans).
5. **Audit + tests** — every guardrail decision writes an `audit_log` row via `app_rt`;
   `tests/test_guardrails.py` asserts PII redaction, jailbreak block, prompt-leak
   catch, and a matching audit row each; CI eval gate stays green through the guards.

**DoD:** guardrails catch injected PII and jailbreaks; the event is logged (audit
table) — alerting itself deferred to Phase 5.

**Deferred (WIP=1):** alerting/dashboards → Phase 5; garak/PyRIT red-team → Phase 4;
per-room guardrail config → backlog.

## 2026-07-16 — Phase 2: rooms, membership & RLS isolation (DONE)

**Done**
- **Schema (`migrations/0002`):** `users`, `rooms`, `memberships` (M:N);
  `documents.room_id` + `chunks.room_id` (denormalized) with a **composite FK**
  `(document_id, room_id) → documents(id, room_id)` so a chunk's room can't drift
  from its document's (FK checks bypass RLS — this is the real integrity guard).
  Non-breaking backfill into a system `legacy` room when prior data exists.
- **Isolation via non-owner role:** migration creates `app_rt` (LOGIN,
  NOSUPERUSER, NOBYPASSRLS), password from `APP_RT_PASSWORD` env (never hardcoded
  — gitleaks). RLS ENABLEd on all four tables; read (`USING`) + write
  (`WITH CHECK`) policies keyed on `NULLIF(current_setting('app.user_id',true),'')::int`.
  Deny-by-default. `app` (owner/superuser) bypasses for migrations + operator seed.
- **Auth (`app/auth.py`):** bcrypt + pyjwt HS256; `get_current_user` Bearer dep.
- **Request path:** `session_for_user()` opens an `app_rt` connection, `SET LOCAL
  app.user_id` (transaction-local), yields. `retrieve()`/`ingest_text()` take that
  RLS-scoped conn; `rag.answer(room_id, user_id)` retrieves within it.
- **API:** `/auth/register|login`, `POST /rooms` (RLS bootstrap: room + own
  membership in one tx), `POST /rooms/{id}/members` (owner-only), `POST
  /rooms/{id}/documents` (the first "writer" — member uploads under write-side
  RLS), `/chat` requires Bearer + room_id. Non-member → **404** (anti-enumeration).
- **Eval gate:** seeds a dedicated eval room and retrieves as a member, so the
  deterministic gate now runs *through* RLS.

**Verified (DoD)**
- `tests/test_isolation.py` (15 tests total green): DB-layer non-member read = 0
  rows; deny-by-default (no identity) = 0 rows; non-member **write** blocked; API
  non-member `/chat` + upload = 404; owner-only membership = 403; positive shared
  room; RLS bootstrap. `python -m eval.gate` → 6/6, GATE PASSED. `ruff` clean.
- End-to-end smoke: register → create room → `POST /documents` (1 chunk) →
  retrieve as member (score 0.87). Cross-room read/write denied.
- CI: `lint-test` now runs migrations against a pg service so isolation tests run;
  both jobs provision `app_rt` via the migration.

**Decisions / notes (see ADR-004)**
- RLS only bites for **non-owner** roles → the whole point of `app_rt`.
- Two gotchas that cost time & are now pinned by tests/ADR: (1) `INSERT ... ON
  CONFLICT` on an RLS table needs the row to be SELECT-visible to the writer —
  an owner adding another user's membership isn't, so it raised a spurious RLS
  error; switched to plain INSERT + idempotent UniqueViolation handling. (2) the
  membership-insert/rooms-read policies must include an owner clause so the
  bootstrap (create room → add own membership) resolves before any membership row
  exists.
- Deferred (`docs/backlog.md`): RBAC/owner-only writes, org umbrella, room
  CRUD/UI, email + agent writers (→ Phase C), user-enum hardening, multipart upload.

**Next**
- Phase 3 — guardrails (llm-guard), PII records under RLS (LLM02/LLM07).

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