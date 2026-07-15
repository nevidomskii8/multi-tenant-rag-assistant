# Roadmap: A Multi-Tenant LLM Assistant Based on Private Data (B → C)

A pet project to improve my skills in **DevOps** and **cybersecurity**. Inference runs
on the **Claude API** (pennies at dev volume, zero load on local hardware). All the
workload runs on my home server `skywalker`; my MacBook serves as the control plane
(SSH / VNC / WireGuard).

---

## 0. Your infrastructure (the backbone of everything)

```
MacBook (control plane) ── WireGuard VPN · SSH · VNC
   │
skywalker — Ubuntu 24.04, KVM host
   ├── Docker ── the project's application stack (FastAPI + Postgres/pgvector + nginx),
   │             Portainer :9000, Grafana :3000, observability, LocalEmu
   │
   └── labnet (10.10.10.0/24, virbr1) — ISOLATED ZONE
         ├── Kali               ← offensive (red-team: garak/PyRIT/…)
         ├── Metasploitable 2/3 ← bonus: classic pentest range
         └── (new VM)           ← LLM-attack detonation + EchoLeak agent (phases 4, 7)

Outbound demo: WireGuard + DuckDNS (jack-sky-home.duckdns.org)
Inference: Claude API (instead of a local model)
```

**Key advantage:** you already physically separate the **trusted prod stack (Docker)**
from the **untrusted detonation zone (labnet/KVM)**. That is exactly the "isolation
matched to trust level" principle you'd normally have to build from scratch — you have
it at the network layer.

---

## 1. What we're building

**Phase B** — a multi-tenant RAG assistant built around **rooms**: a room is a
shareable RAG space with its own data. **Multiple users** access a room via
**membership** (many-to-many). The room — not a per-user tenant — is the unit of
isolation: a user sees data only for the rooms they belong to.

**Phase C** — the same stack + an agent layer with tools (email/calendar/files) and
memory; we reproduce and then block EchoLeak-class attacks (indirect prompt injection
via an incoming email).

> C is an extension of B, not a separate project. Once B is complete, it is already a
> full-fledged project; C is an ambitious expansion.

---

## 2. The vertical

**Customer-support / team assistant:** a room = a shared knowledge space → a clean
access boundary; private data = tickets/orders/profiles → tangible PII; the knowledge
base → the source for RAG; in C the agent acts on tickets/emails → the stage for
EchoLeak. Interchangeable (HR / finance / legal / medical records). **Data is always
synthetic.**

---

## 3. What's already done (we won't be reworking it)

The host level is effectively closed — this is your DevOps/security head start:

- ✓ Ubuntu 24.04, Docker 29 + Compose, Portainer `:9000`
- ✓ SSH hardening: ed25519, `PasswordAuthentication no`, `PermitRootLogin no`, port 2222
- ✓ UFW (deny incoming) + fail2ban (jail sshd)
- ✓ WireGuard VPN + DuckDNS — a ready-to-use channel for an external demo
- ✓ Grafana `:3000` — reuse it in observability
- ✓ KVM lab: Kali + Metasploitable 2/3 on an isolated `labnet`

All that's left is to build **the project itself** on top of this.

---

## 4. Stack — where everything happens

| Layer | Where / what | Note |
|---|---|---|
| Inference | **Claude API** | instead of Ollama, zero hardware load |
| App | Docker on `skywalker`: FastAPI + nginx | next to Portainer |
| Vector + relational | Postgres + pgvector (Docker) | + row-level security |
| Documents | MinIO (Docker) or a volume | S3 in the AWS phase |
| Auth / membership | local JWT issuer | Cognito in the AWS phase |
| Guardrails | llm-guard (Docker, calls Claude API) | Bedrock Guardrails on AWS |
| Observability | Prometheus + Loki + Langfuse (Docker) + your Grafana | reuse `:3000` |
| IaC emulation | LocalEmu / LocalStack (Docker) | test Terraform without AWS |
| Red-team | garak / PyRIT / Promptfoo / DeepTeam from **Kali** | target = the app API |
| Detonation / agent C | a dedicated **KVM VM on labnet** | isolated from prod and LAN |
| CI/CD | GitHub Actions | + DevSecOps scans |
| AWS (demo) | Terraform: ECS/Lambda + RDS + S3 + Cognito + API GW + WAF | for the cloud-deploy skill |

---

## 5. Principles

- **Self-hosted on `skywalker`.** AWS is for cloud-deploy demos only (a VPN covers access).
- **Synthetic data.** Never real PII.
- **IaC for everything** — tested on LocalEmu, no "clicking" in the console.
- **Security = automated tests + regression.** Every bug is closed by a fix and a guard test.
- **Isolation matched to trust level** — prod in Docker, detonation in `labnet`/KVM
  (already separated for you at the network layer).
- **Each component = an OWASP item.**

---

## 6. Phases

> Milestones: **MVP** — end of Phase 2; **full B** — end of Phase 6 (or Phase 5 if you
> skip the AWS demo); **C** — Phase 7. Time estimates are rough.

### Phase 0 — Project scaffolding (host already done) (~0.5 weekend) — DONE
- **Do:** repo structure; `docker-compose.yml` for the app stack on `skywalker`; `.env`;
  pre-commit; basic GitHub Actions (lint + unit tests); gitleaks. *(Host hardening — ✓
  already in place.)*
- **DevOps:** project scaffolding, CI, secret hygiene.
- **Security:** gitleaks in CI, dependency scanning.
- **Done when:** `docker compose up` on `skywalker` brings up the scaffold; CI green on push.

### Phase 1 — RAG core, single-room (~1–2 weekends) — DONE
- **Do:** inference via the **Claude API**; ingestion (chunk → embed → pgvector);
  retrieval; chat API + minimal UI; DB migrations; a first eval suite (Promptfoo/DeepEval:
  faithfulness, relevancy) wired into CI as a gate.
- **Security:** treat LLM output as untrusted (LLM05); input validation.
- **Done when:** RAG answers over synthetic data; eval gate green in CI.

### Phase 2 — Rooms, membership & isolation, the heart of B (~2 weekends) — NEXT
- **Do:** `users`, `rooms`, `memberships` (many-to-many); a local JWT issuer (login →
  token carrying `user_id`); Postgres **Row-Level Security** enforced through a
  **non-owner runtime DB role** + a per-request session variable (`SET LOCAL
  app.user_id`); room-scoped ingestion and retrieval; `/chat` requires auth + a target
  room. Flat membership: a member reads/queries a room; the creator (`owner`) manages
  documents and members.
- **Security:** broken access control — tests that **actively try** to read another
  room's data and assert denial; vector/embedding leakage (LLM08); sensitive-info
  disclosure (LLM02); deny-by-default RLS.
- **Done when:** a non-member cannot read a room's data — **proven by automated tests**
  at both the API layer and the raw DB layer.

> **Deferred to Phase 6.5:** the first Terraform (RDS/S3/Cognito) on LocalStack. Kept out
> of Phase 2 to hold WIP=1 and keep focus on the security core; the schema is still moving
> (Phase 3 adds PII tables/logs), so IaC written now would drift before it's ever applied.
>
> **Out of scope for Phase 2 (backlog):** RBAC roles (owner/editor/viewer), an
> org/company umbrella above rooms, full room CRUD/UI, user-enumeration hardening.

### Phase 3 — Guardrails & hardening, the security layer of B (~1–2 weekends)
- **Do:** llm-guard as a container on input/output: PII detection and redaction, prompt-
  injection detection, topic/output filters; system-prompt protection; structured audit
  logs. Introduce private PII records (tickets/orders/profiles), room-scoped and under RLS.
- **Security:** PII leak (LLM02), system prompt leakage (LLM07).
- **Done when:** guardrails catch injected PII and jailbreaks; the event is logged and alerted.

### Phase 4 — Red-team & DevSecOps (~2 weekends) ← `labnet` comes alive
- **Do:** garak / Promptfoo red-team / DeepTeam / PyRIT from **Kali** against the app
  API; map to OWASP. Detonate any hostile payload in a **dedicated VM on labnet** —
  isolated. DevSecOps scanners in CI: trivy (images), checkov/tfsec (IaC), semgrep
  (SAST), gitleaks; SBOM. Every finding gets a regression test.
- **Bonus track:** Metasploitable 2/3 — classic pentest (nmap / Metasploit from Kali)
  for cyber-security breadth, independent of the LLM part.
- **Security:** the full OWASP LLM Top 10 as automated tests; supply chain (LLM03);
  data-poisoning awareness (LLM04).
- **Done when:** red-team + scans run in CI; findings are tracked and pinned by
  regression tests.

### Phase 5 — Observability (~1 weekend) ← reuse Grafana
- **Do:** ship Prometheus + Loki + Langfuse as containers; connect to the existing
  **Grafana `:3000`**; metrics (latency, tokens, **Claude API cost**, retrieval quality),
  logs, LLM tracing; dashboards-as-code; alerts; rate limiting.
- **Security:** audit logs, access-anomaly detection, rate limiting against unbounded
  consumption / **denial-of-wallet** (LLM10) — especially important with a paid API.
- **Done when:** dashboards are live; alerts fire on anomalies and token-cost spikes.

### Phase 6 — AWS deploy, the cloud-deploy skill (~1 weekend)
- **Do:** `terraform apply` to real AWS (ECS Fargate or Lambda + RDS pgvector + S3 +
  Cognito + API Gateway + WAF) on free credits; deploy via CD; demo; `terraform destroy`.
  *(AWS isn't needed for access — you have VPN; this is for the real cloud-deploy skill.)*
- **DevOps:** cloud deploy, CD, ephemeral environments, FinOps (budget alerts,
  scale-to-zero), WAF, IAM least-privilege, secrets in Secrets Manager, encryption.
- **Done when:** the app runs in AWS for the demo window; torn down cleanly; spend within credits.

### Phase 6.5 — IaC foundation on LocalStack (~1 weekend) — split out of Phase 2
- **Do:** the first Terraform (RDS pgvector + S3 + Cognito), tested on **LocalStack /
  LocalEmu** before any real AWS. Runs right before Phase 6 so it targets a settled schema.
- **DevOps:** IaC discipline, no console clicking, emulator-first testing.
- **Done when:** `terraform apply` / `destroy` against the emulator provisions the stack
  reproducibly.

### Phase 7 — Agent C + EchoLeak (~2–3 weekends) ← detonation on `labnet`
- **Do:** an agent layer (a framework locally on `skywalker` / AgentCore on AWS); tools
  email(mock)/calendar/files; memory with namespace isolation. EchoLeak scenario:
  **Kali** sends a malicious email carrying an indirect injection → the agent (in an
  **isolated VM on labnet**) attempts to exfiltrate data → then the defense: content-
  provenance checks, tool-permission scoping, human-in-the-loop for dangerous actions.
  Detonation strictly in labnet.
- **Security:** excessive agency (LLM06), indirect prompt injection, blast-radius control.
- **Done when:** the exploit works on the undefended build **and** the defense blocks it
  on the hardened build.

### Phase 8 — Polish & portfolio (~1 weekend)
- **Do:** README with a diagram (including the `skywalker`/labnet topology) and a threat
  model; demo over WireGuard; write-ups of each OWASP case (attack + defense); a runbook;
  CI badges; (optional) an article.
- **Done when:** the repo is self-contained and presentable.

---

## 7. OWASP LLM Top 10 coverage

| Risk | Where it's addressed |
|---|---|
| LLM01 Prompt injection | Phases 1 (awareness), 3 (guardrails), 4 (red-team), 7 (indirect/EchoLeak) |
| LLM02 Sensitive info disclosure / PII | Phases 2 (room isolation) + 3 (redaction) |
| LLM03 Supply chain | Phase 4 (dependency scans, SBOM) |
| LLM04 Data / model poisoning | Phases 1 / 4 (data provenance) |
| LLM05 Improper output handling | Phase 1 |
| LLM06 Excessive agency | Phase 7 |
| LLM07 System prompt leakage | Phase 3 |
| LLM08 Vector / embedding weaknesses | Phase 2 (cross-room retrieval) |
| LLM09 Misinformation | Phase 1 (eval: faithfulness / hallucination) |
| LLM10 Unbounded consumption | Phase 5 (rate limiting, API-cost monitoring) |

---

## 8. Budget and traps

- **Claude API:** watch tokens (monitoring in Phase 5 + a budget alert). Denial-of-wallet
  is both a real risk for you and a security case (LLM10).
- **AWS — for demos only.** Avoid expensive services: OpenSearch Serverless (~$345/mo →
  pgvector), NAT Gateway (~$33/mo), dangling Elastic IPs, forgotten EBS.
- **Always `terraform destroy`** after a demo. A $1 budget alert before the first `apply`.
- **The AWS free plan closes after 6 months / on credit exhaustion** — plan the demo
  inside that window.

---

## 9. What you get

- **DevOps:** Docker/Compose on a real self-hosted server, Terraform/IaC with local
  testing on LocalEmu, CI/CD with gates, observability on top of your own Grafana,
  ephemeral AWS deploy, FinOps.
- **Cyber security:** the full OWASP LLM Top 10 in practice, red-teaming from Kali
  (garak/PyRIT/Promptfoo/DeepTeam), guardrails, DevSecOps in CI, multi-tenant room
  isolation, a real attack (EchoLeak) reproduced and closed inside an isolated KVM
  sandbox, plus a classic pentest on Metasploitable.
- **Artifact:** a deployable multi-tenant LLM application with proven isolation,
  automated security, and observability — on infrastructure you run yourself.

---

## Key resources

- Claude API (inference) — https://docs.claude.com
- LocalEmu (free AWS emulator) — https://localemu.cloud · LocalStack — https://localstack.cloud
- AWS Free Tier ($200 / 6 months) — https://aws.amazon.com/free/
- OWASP LLM Top 10 — https://genai.owasp.org/llm-top-10/
- DeepTeam (OWASP red-team) — https://trydeepteam.com · Promptfoo — https://promptfoo.dev
- Red-team tooling (overview of garak / PyRIT / Promptfoo / DeepTeam) — https://appsecsanta.com/ai-security-tools/llm-red-teaming
- llm-guard (guardrails, ProtectAI) — https://github.com/protectai/llm-guard
- Bedrock Knowledge Bases / AgentCore (for the AWS phase) — https://aws.amazon.com/bedrock/