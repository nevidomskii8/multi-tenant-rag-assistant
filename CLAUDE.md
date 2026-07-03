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
- **Code: not started.**
- **Next up — Phase 0:** project scaffolding — repo structure, `docker-compose.yml`
  (FastAPI + Postgres/pgvector + nginx), first GitHub Actions workflow
  (lint + tests), `.gitignore`, pre-commit, gitleaks. *Done when* `docker compose up`
  brings up the skeleton and CI is green on push.
- Keep this section current — it is the fastest way for a new session to know
  where we are.
