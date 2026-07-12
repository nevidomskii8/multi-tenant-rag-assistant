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
- **Next up — Phase 2:** multi-tenancy + isolation. Tenants, per-tenant data,
  Postgres Row-Level Security on `chunks`/`documents` (LLM08/LLM02). This is the
  core of B. UI (Phase 1 step 5) deferred to `docs/backlog.md`.
- Keep this section current — it is the fastest way for a new session to know
  where we are.
