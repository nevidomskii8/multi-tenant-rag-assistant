# CLAUDE.md

Orientation file for AI assistants (and humans) working on this project.
**Read this first.** For full phase detail, see `docs/roadmap.md`.

## What this is

A multi-tenant LLM assistant over private data, built as a learning + portfolio
project. Two goals drive every decision: practice **DevOps** and work through
**cyber security** cases (OWASP LLM Top 10).

- **Domain:** customer-support assistant (multi-tenant SaaS). Tenant = client
  company; each has its own knowledge-base docs + private records (tickets,
  orders, customer profiles). A user belongs to one tenant and sees only its data.
- **Phase B** — multi-tenant RAG assistant with tenant isolation, access control,
  and PII guardrails. This is the core project.
- **Phase C** — adds an agent layer (email/calendar/file tools + memory) and
  reproduces *and defends against* an EchoLeak-style indirect prompt-injection
  attack. C is an extension on top of B, not a separate project.
- Solo developer ("Jack"). Data is always **synthetic** — never real PII.

## Infrastructure (where things run)

> Internal specifics (hostnames, IPs, ports, network topology) live in
> `docs/infra.local.md`, which is gitignored. This section stays generic on purpose.

- **Dev machine** — write code here, run the stack locally (`docker compose up`)
  for fast iteration, commit, and `git push`.
- **GitHub (public repo)** — central hub / source of truth. CI runs here
  (GitHub Actions). Issues + Projects track work.
- **Home server** — Linux KVM host running Docker. Two roles: deploy target, and
  host of the isolated security lab.
- **Isolated lab network** — segregated network for the attacker/target VMs. All
  hostile-code detonation and the EchoLeak agent run HERE, never on the dev machine.
- **Remote access:** VPN (demos can be shown over VPN).
- **AWS** — used only for short, time-boxed demos on free-tier credits, to
  practice real cloud deployment. Not required for the project to run.

## Stack

- **Inference:** Claude API. No local model (Ollama) — keeps the dev stack light.
- **Vector + relational store:** Postgres + pgvector, with row-level security.
  NOT OpenSearch.
- **App:** FastAPI + nginx, containerized with Docker / Docker Compose.
- **Guardrails:** llm-guard (local) / Bedrock Guardrails (AWS).
- **Agent (Phase C):** a framework (Strands / LangChain / LlamaIndex) locally,
  or Bedrock AgentCore on AWS.
- **IaC:** Terraform, tested locally on LocalEmu / LocalStack before any real AWS.
- **CI/CD:** GitHub Actions.
- **Observability:** Prometheus + Loki + Langfuse, wired into the existing Grafana.
- **Red-team:** garak, PyRIT, Promptfoo, DeepTeam (run from Kali, target the app API).
- **DevSecOps scanners:** trivy, gitleaks, checkov/tfsec, semgrep (in CI).

## Key decisions (do not re-litigate)

- **Claude API over a local model** — no RAM/GPU constraint; dev stack stays light.
- **Docker for the app stack; KVM/isolated lab for untrusted code** — isolation
  matched to trust level. Trusted app → containers (cloud parity, efficiency,
  industry standard). Malware / red-team detonation → isolated VM on the lab network.
- **pgvector over OpenSearch Serverless** — OpenSearch Serverless costs ~$345/mo
  even when idle; pgvector is near-free at this scale.
- **GitHub public over a self-hosted forge** — portfolio visibility + easiest CI
  to learn. Self-hosted forge / self-hosted runners are a later stretch.
- **Develop on the dev machine, deploy + detonate on the home server.**

## How we work

- **WIP = 1.** One phase at a time. Finish, then take the next.
- **DoD is a stop signal.** Each phase has a "done when" in the roadmap — hit it,
  close it, move on. Do not gold-plate or expand scope.
- **`main` is always runnable.** Every phase ends with something demonstrable.
- **Security = tests.** Every finding gets a fix AND a regression test that keeps
  it from coming back. Map work to OWASP LLM Top 10.
- **Tangents go to the backlog** (a GitHub issue labeled `backlog`); they do not
  interrupt the current phase.
- **Tracking:** GitHub Issues = tasks, Milestones = phases, Projects = board.
  Update `docs/devlog.md` at the end of each session; record decisions as ADRs in
  `docs/decisions.md`.

## Working with the maintainer

- Be **concise**; cite sources for factual claims.
- **Do not add complexity or assumptions without justification** — Jack will (and
  should) push back. State any assumption explicitly.

## Current status

- Planning complete. Roadmap written (`docs/roadmap.md`); infra mapped above.
- **Phase 0 — DONE.** Scaffolding: `docker-compose.yml` (FastAPI +
  Postgres/pgvector + nginx), `/health` + pytest, pydantic-settings config, CI
  (ruff + pytest + gitleaks), pre-commit. Verified `docker compose up` →
  `/health` returns `ok`; CI green.
- **Phase 1 — DONE (single-tenant RAG core).** End-to-end pipeline:
  - **Schema/migrations:** Alembic + `documents`/`chunks(embedding vector(384))`
    + HNSW cosine index (`migrations/`, `app/config.py`).
  - **Ingestion:** `data/kb/*.md` → token-based chunking (with boundary check)
    → e5 `passage:` embeddings → pgvector (`app/ingest.py`, `app/embeddings.py`,
    `app/db.py`).
  - **Retrieval:** e5 `query:` → cosine top-k over pgvector (`app/retrieval.py`).
  - **Chat:** `POST /chat` → retrieve → context-as-data prompt → Claude →
    `{answer, sources}` (`app/rag.py`, `app/main.py`). Guardrails: injection
    hygiene (LLM01), input/`max_tokens` caps (LLM10).
  - **Eval:** golden set + deterministic retrieval gate in CI (top-1, no key) +
    local LLM-judge faithfulness/relevancy (`eval/`, `.github/workflows/ci.yml`).
  - Inference `claude-haiku-4-5` (dev); embeddings `multilingual-e5-small` local.
  - *DoD met:* RAG answers over synthetic data; eval gate green in CI.
- **Phase 2 — DONE (rooms, membership & RLS isolation).** The security core of B:
  - **Room model:** `users`/`rooms`/`memberships` (M:N). A room is a shareable RAG
    space; flat membership (owner adds members); the room is the isolation unit
    (`migrations/0002`, ADR-004). `documents`/`chunks` gained `room_id` (chunks
    denormalized + composite FK to keep chunk↔document rooms in lock-step).
  - **Isolation:** Postgres RLS enforced through a **non-owner runtime role
    `app_rt`** (superuser/owner bypass RLS, so the request path must not be `app`)
    + per-request `SET LOCAL app.user_id`; read + write policies, deny-by-default.
    `app` stays admin-only (migrations/seed). Password from `APP_RT_PASSWORD` env.
  - **Auth:** local JWT issuer (bcrypt + pyjwt HS256), `get_current_user` Bearer.
  - **API:** `/auth/register|login`, `POST /rooms`, `/rooms/{id}/members` (owner),
    `/rooms/{id}/documents` (member upload — first "writer" under write-side RLS),
    `/chat` (Bearer + room_id; non-member → 404, anti-enumeration).
  - *DoD met:* a non-member can't read OR write a room's data — proven by
    `tests/test_isolation.py` at the DB layer and the API layer; eval gate runs
    through RLS and stays green in CI.
- **Next up — Phase 3 (PLANNED, not started):** guardrails (llm-guard on
  input/output: PII redaction, injection detection), private PII records
  (tickets/orders/profiles) room-scoped under RLS, audit logs (LLM02/LLM07).
  Architecture locked in **ADR-005** (llm-guard **sidecar** · Postgres `audit_log`
  under RLS · PII **embedded into RAG**); build order + DoD in `docs/devlog.md`
  (2026-07-21 entry). See also `docs/roadmap.md` Phase 3.
- Keep this section current — it is the fastest way for a new session to know
  where we are.
