# Roadmap: мультитенантный LLM-ассистент на приватных данных (B → C)

Pet-проект для прокачки **DevOps** и **cyber security**. Инференс — **Claude API** (на dev-объёме копейки, ноль нагрузки на железо). Вся рабочая нагрузка — на домашнем сервере `skywalker`; MacBook — control plane (SSH / VNC / WireGuard).

---

## 0. Твоя инфраструктура (на чём всё крутится)

```
MacBook (control plane) ── WireGuard VPN · SSH · VNC
   │
skywalker — Ubuntu 24.04, KVM-хост
   ├── Docker ── app-стек проекта (FastAPI + Postgres/pgvector + nginx),
   │             Portainer :9000, Grafana :3000, observability, LocalEmu
   │
   └── labnet (10.10.10.0/24, virbr1) — ИЗОЛИРОВАННАЯ зона
         ├── Kali               ← атакующая (red-team: garak/PyRIT/…)
         ├── Metasploitable 2/3 ← бонус: классический пентест-полигон
         └── (новая VM)         ← детонация LLM-атак + агент EchoLeak (фазы 4, 7)

Демо наружу: WireGuard + DuckDNS (jack-sky-home.duckdns.org)
Инференс: Claude API (вместо локальной модели)
```

**Ключевой плюс:** у тебя уже физически разведены **доверенный прод-стек (Docker)** и **недоверенная зона детонации (labnet/KVM)**. Это ровно принцип «изоляция под уровень доверия», который обычно приходится строить с нуля, — у тебя он есть на уровне сети.

---

## 1. Что строим

**Фаза B** — мультитенантный RAG-ассистент: несколько тенантов (клиентов), у каждого свои документы и приватные записи; пользователь принадлежит тенанту и видит только его данные.

**Фаза C** — тот же стек + слой агента с инструментами (почта/календарь/файлы) и памятью; воспроизводим и закрываем атаки класса EchoLeak (indirect prompt injection через входящее письмо).

> C — надстройка над B, не отдельный проект. Завершённый B — уже полноценный проект; C — амбициозное расширение.

---

## 2. Вертикаль

**Ассистент клиентской поддержки (multi-tenant SaaS):** тенант = компания → чистая граница доступа; приватные данные = тикеты/заказы/профили → наглядный PII; база знаний → источник для RAG; в C агент действует над тикетами/письмами → сцена для EchoLeak. Взаимозаменяема (HR / финансы / юридический / медкарта). **Данные всегда синтетические.**

---

## 3. Что уже готово (не переделываем)

Host-уровень фактически закрыт — это твой задел по DevOps/security:

- ✓ Ubuntu 24.04, Docker 29 + Compose, Portainer `:9000`
- ✓ SSH hardening: ed25519, `PasswordAuthentication no`, `PermitRootLogin no`, порт 2222
- ✓ UFW (deny incoming) + fail2ban (jail sshd)
- ✓ WireGuard VPN + DuckDNS — готовый канал для демо наружу
- ✓ Grafana `:3000` — переиспользуем в observability
- ✓ KVM-лаба: Kali + Metasploitable 2/3 на изолированной `labnet`

Остаётся построить **сам проект** поверх этого.

---

## 4. Стек — где что крутится

| Слой | Где / чем | Заметка |
|---|---|---|
| Инференс | **Claude API** | вместо Ollama, ноль нагрузки на железо |
| App | Docker на `skywalker`: FastAPI + nginx | рядом с Portainer |
| Vector + реляционка | Postgres + pgvector (Docker) | + row-level security |
| Документы | MinIO (Docker) или том | S3 на AWS-фазе |
| Auth / tenancy | локальный JWT-issuer | Cognito на AWS-фазе |
| Guardrails | llm-guard (Docker, дёргает Claude API) | Bedrock Guardrails на AWS |
| Observability | Prometheus + Loki + Langfuse (Docker) + твоя Grafana | переиспользуешь `:3000` |
| IaC-эмуляция | LocalEmu / LocalStack (Docker) | тест Terraform без AWS |
| Red-team | garak / PyRIT / Promptfoo / DeepTeam с **Kali** | цель — API приложения |
| Детонация / агент C | отдельная **KVM-VM на labnet** | изоляция от прода и LAN |
| CI/CD | GitHub Actions | + DevSecOps-сканы |
| AWS (демо) | Terraform: ECS/Lambda + RDS + S3 + Cognito + API GW + WAF | ради навыка cloud-деплоя |

---

## 5. Принципы

- **Self-hosted на `skywalker`.** AWS — только под демо облачного деплоя (для доступа есть VPN).
- **Синтетические данные.** Никаких реальных PII.
- **IaC на всё** — тест на LocalEmu, никакого «кликанья» в консоли.
- **Security = автотесты + регресс.** Каждый баг закрывается фиксом и тестом-стражем.
- **Изоляция под уровень доверия** — прод в Docker, детонация в `labnet`/KVM (у тебя уже разведено).
- **Каждый компонент = пункт OWASP.**

---

## 6. Фазы

> Вехи: **MVP** — конец Фазы 2; **полный B** — конец Фазы 6 (или Фазы 5, если AWS-демо опускаешь); **C** — Фаза 7. Оценки времени ориентировочные.

### Фаза 0 — Каркас проекта (host уже готов) (~0.5 уикенда)
- **Делаешь:** структура репо; `docker-compose.yml` для app-стека на `skywalker`; `.env`; pre-commit; базовый GitHub Actions (lint + unit-тесты); gitleaks. *(Host-hardening — ✓ уже есть.)*
- **DevOps:** scaffolding проекта, CI, гигиена секретов.
- **Security:** gitleaks в CI, скан зависимостей.
- **Готово:** `docker compose up` на `skywalker` поднимает каркас; CI зелёный на push.

### Фаза 1 — Ядро RAG, single-tenant (~1–2 уикенда)
- **Делаешь:** инференс через **Claude API**; ingestion (chunk → embed → pgvector); retrieval; чат-API + минимальный UI; миграции БД; первый eval-сьют (Promptfoo/DeepEval: faithfulness, relevancy) в CI как гейт.
- **Security:** обращение с выводом LLM как с недоверенным (LLM05); валидация ввода.
- **Готово:** RAG отвечает по синтетике; eval зелёный в CI.

### Фаза 2 — Мультитенантность и контроль доступа, сердце B (~2 уикенда)
- **Делаешь:** tenants/users; JWT с `tenant_id`; row-level security в Postgres; tenant-фильтр на retrieval; префиксы документов по тенантам; первый Terraform (RDS/S3/Cognito), протестированный на LocalEmu.
- **Security:** broken access control — тесты, что **пытаются** достать чужие данные и проверяют отказ; vector/embedding leakage (LLM08); pool vs silo.
- **Готово:** тенант A не читает данные B — доказано автотестами.

### Фаза 3 — Guardrails и хардненинг, security-слой B (~1–2 уикенда)
- **Делаешь:** llm-guard контейнером на вход/выход: детект и редакция PII, детект prompt injection, фильтры тем/вывода; защита системного промпта; структурные аудит-логи.
- **Security:** PII-leak (LLM02), system prompt leakage (LLM07).
- **Готово:** guardrails ловят подсунутый PII и jailbreak; событие логируется и алертится.

### Фаза 4 — Red-team и DevSecOps (~2 уикенда) ← тут оживает `labnet`
- **Делаешь:** garak / Promptfoo red-team / DeepTeam / PyRIT **с Kali** по API приложения; маппинг на OWASP. Любую враждебную нагрузку детонируешь в **отдельной VM на labnet** — изолированно. DevSecOps-сканеры в CI: trivy (образы), checkov/tfsec (IaC), semgrep (SAST), gitleaks; SBOM. На каждый найденный баг — регресс-тест.
- **Бонус-дорожка:** Metasploitable 2/3 — классический пентест (nmap / Metasploit с Kali) для широты cyber security, независимо от LLM-части.
- **Security:** полный OWASP LLM Top 10 как автотест; supply chain (LLM03); data poisoning awareness (LLM04).
- **Готово:** red-team + сканы гоняются в CI; находки трекаются и закреплены регресс-тестами.

### Фаза 5 — Observability (~1 уикенд) ← переиспользуешь Grafana
- **Делаешь:** доставляешь Prometheus + Loki + Langfuse контейнерами; подключаешь к существующей **Grafana `:3000`**; метрики (latency, токены, **стоимость Claude API**, качество retrieval), логи, трейсинг LLM; дашборды-as-code; алерты; rate limiting.
- **Security:** аудит-логи, детект аномалий доступа, rate limiting против unbounded consumption / **denial-of-wallet** (LLM10) — особенно важно с платным API.
- **Готово:** дашборды живые; алерты срабатывают на аномалии и всплески стоимости токенов.

### Фаза 6 — AWS деплой, навык cloud-деплоя (~1 уикенд)
- **Делаешь:** `terraform apply` в реальный AWS (ECS Fargate или Lambda + RDS pgvector + S3 + Cognito + API Gateway + WAF) на бесплатных кредитах; деплой через CD; демо; `terraform destroy`. *(Для доступа AWS не нужен — у тебя VPN; это ради навыка реального облачного деплоя.)*
- **DevOps:** cloud-деплой, CD, эфемерные окружения, FinOps (бюджет-алерты, scale-to-zero), WAF, IAM least-privilege, секреты в Secrets Manager, шифрование.
- **Готово:** приложение в AWS на время демо; снесено начисто; расход в пределах кредитов.

### Фаза 7 — Агент C + EchoLeak (~2–3 уикенда) ← детонация на `labnet`
- **Делаешь:** слой агента (фреймворк локально на `skywalker` / AgentCore на AWS); инструменты почта(mock)/календарь/файлы; память с namespace-изоляцией. Сценарий EchoLeak: **Kali** шлёт вредоносное письмо с indirect injection → агент (в **изолированной VM на labnet**) пытается слить данные → затем защита: проверка провенанса контента, скоупинг прав инструментов, human-in-the-loop для опасных действий. Детонация — строго в labnet.
- **Security:** excessive agency (LLM06), indirect prompt injection, контроль blast-radius.
- **Готово:** эксплойт работает на незащищённой версии **и** защита блокирует его на защищённой.

### Фаза 8 — Полировка и портфолио (~1 уикенд)
- **Делаешь:** README с диаграммой (включая твою топологию `skywalker`/labnet) и threat-model; демо через WireGuard; разборы каждого OWASP-кейса (атака + защита); runbook; CI-бейджи; (опц.) статья.
- **Готово:** репо самодостаточно и презентабельно.

---

## 7. Покрытие OWASP LLM Top 10

| Риск | Где закрываешь |
|---|---|
| LLM01 Prompt injection | Фазы 1 (осознание), 3 (guardrails), 4 (red-team), 7 (indirect/EchoLeak) |
| LLM02 Sensitive info disclosure / PII | Фаза 3 |
| LLM03 Supply chain | Фаза 4 (скан зависимостей, SBOM) |
| LLM04 Data / model poisoning | Фазы 1 / 4 (провенанс данных) |
| LLM05 Improper output handling | Фаза 1 |
| LLM06 Excessive agency | Фаза 7 |
| LLM07 System prompt leakage | Фаза 3 |
| LLM08 Vector / embedding weaknesses | Фаза 2 (cross-tenant retrieval) |
| LLM09 Misinformation | Фаза 1 (eval: faithfulness / hallucination) |
| LLM10 Unbounded consumption | Фаза 5 (rate limiting, мониторинг стоимости API) |

---

## 8. Бюджет и ловушки

- **Claude API:** следи за токенами (мониторинг в Фазе 5 + бюджет-алерт). Denial-of-wallet — это и твой реальный риск, и security-кейс (LLM10).
- **AWS — только под демо.** Избегай дорогих сервисов: OpenSearch Serverless (~$345/мес → pgvector), NAT Gateway (~$33/мес), висящие Elastic IP, забытые EBS.
- **Всегда `terraform destroy`** после демо. Бюджет-алерт на $1 до первого `apply`.
- **Free-план AWS закрывается через 6 мес / по исчерпании кредитов** — планируй демо в окне.

---

## 9. Что получишь

- **DevOps:** Docker/Compose на реальном self-hosted сервере, Terraform/IaC + локальное тестирование на LocalEmu, CI/CD с гейтами, observability поверх своей Grafana, эфемерный AWS-деплой, FinOps.
- **Cyber security:** весь OWASP LLM Top 10 на практике, red-teaming с Kali (garak/PyRIT/Promptfoo/DeepTeam), guardrails, DevSecOps в CI, мультитенантная изоляция, воспроизведённая и закрытая реальная атака (EchoLeak) в изолированной KVM-песочнице, плюс классический пентест на Metasploitable.
- **Артефакт:** развёртываемое мультитенантное LLM-приложение с доказанной изоляцией, автоматизированной безопасностью и наблюдаемостью — на инфраструктуре, которую ты держишь сам.

---

## Ключевые ресурсы

- Claude API (инференс) — https://docs.claude.com
- LocalEmu (free эмулятор AWS) — https://localemu.cloud · LocalStack — https://localstack.cloud
- AWS Free Tier ($200 / 6 мес) — https://aws.amazon.com/free/
- OWASP LLM Top 10 — https://genai.owasp.org/llm-top-10/
- DeepTeam (red-team по OWASP) — https://trydeepteam.com · Promptfoo — https://promptfoo.dev
- Red-team тулинг (обзор garak / PyRIT / Promptfoo / DeepTeam) — https://appsecsanta.com/ai-security-tools/llm-red-teaming
- llm-guard (guardrails, ProtectAI) — https://github.com/protectai/llm-guard
- Bedrock Knowledge Bases / AgentCore (для AWS-фазы) — https://aws.amazon.com/bedrock/