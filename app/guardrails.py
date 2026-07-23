"""Client for the llm-guard sidecar (Phase 3).

Wraps the sidecar's `/analyze/prompt` and `/analyze/output` endpoints behind two
calls — `scan_input` and `scan_output` — used on the `/chat` request path.

**Fail-closed (ADR-005).** Any failure to get a clean verdict — sidecar down,
timeout, non-200, unparseable body, or a missing `is_valid` — resolves to
`allowed=False`. A down guard therefore denies the request: it degrades
availability, not safety. The caller turns a disallowed result into a structured
refusal and (Phase 3 step 5) an `audit_log` row; it must never fall back to the
raw model path.

Framework-agnostic on purpose (no FastAPI imports) so it is unit-testable with a
mocked transport.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanResult:
    """Outcome of one guardrail scan.

    allowed   — safe to proceed. False on a policy block OR an infra failure.
    sanitized — the redacted text to use downstream (PII masked by the scanners);
                empty string when not allowed.
    scanners  — raw per-scanner map from the sidecar, kept verbatim for the audit
                detail (never holds the raw PII, only scanner names/scores).
    reason    — None when allowed; "policy" (a scanner blocked) or "unavailable"
                (fail-closed on an infra error).
    """

    allowed: bool
    sanitized: str
    scanners: dict
    reason: str | None


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    """HTTP client for the sidecar, created once. Bearer auth matches the sidecar's
    http_bearer config (GUARDRAILS_AUTH_TOKEN)."""
    headers = {}
    if settings.guardrails_auth_token:
        headers["Authorization"] = f"Bearer {settings.guardrails_auth_token}"
    return httpx.Client(
        base_url=settings.guardrails_url,
        timeout=settings.guardrails_timeout_seconds,
        headers=headers,
    )


def _scan(path: str, payload: dict, sanitized_key: str) -> ScanResult:
    try:
        resp = _client().post(path, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # FAIL-CLOSED: no clean verdict → deny. Log the failure class, not content.
        log.warning("guardrail %s unavailable, failing closed: %s", path, exc.__class__.__name__)
        return ScanResult(allowed=False, sanitized="", scanners={}, reason="unavailable")

    allowed = bool(data.get("is_valid", False))  # missing/false → deny
    return ScanResult(
        allowed=allowed,
        sanitized=data.get(sanitized_key, "") if allowed else "",
        scanners=data.get("scanners", {}),
        reason=None if allowed else "policy",
    )


def scan_input(prompt: str) -> ScanResult:
    """Scan a user prompt (injection detection + PII anonymisation)."""
    return _scan("/analyze/prompt", {"prompt": prompt}, "sanitized_prompt")


def scan_output(prompt: str, output: str) -> ScanResult:
    """Scan the model's answer (PII leakage + system-prompt-canary tripwire)."""
    return _scan("/analyze/output", {"prompt": prompt, "output": output}, "sanitized_output")
