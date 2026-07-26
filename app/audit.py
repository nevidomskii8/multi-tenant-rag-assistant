"""Append-only writer for the guardrail audit trail (Phase 3).

Every guardrail decision on the `/chat` path writes one `audit_log` row through
the `app_rt` runtime role, so RLS scopes each event to its room — a caller reads
only their own rooms' audit trail (proven in tests/test_guardrails.py). `detail`
holds the scanner names/scores and the block reason; it never stores the raw PII
the guard just redacted. Alerting/dashboards over these rows are deferred to
Phase 5 (ADR-005) — this step only makes the events durable and queryable.

Like the ingest helpers, `record_scan` does NOT commit: the caller owns the
transaction (the request path commits when `session_for_user` exits).
"""

import psycopg
from psycopg.types.json import Jsonb

from app.guardrails import ScanResult

INPUT = "input_scan"
OUTPUT = "output_scan"


def record_scan(
    conn: psycopg.Connection,
    *,
    room_id: int,
    user_id: int,
    event_type: str,
    result: ScanResult,
) -> None:
    """Append one guardrail decision. `result.scanners` is stored verbatim (scores
    only — no content), so the row is safe to keep and to expose to room members."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (room_id, user_id, event_type, verdict, detail) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                room_id,
                user_id,
                event_type,
                "allow" if result.allowed else "block",
                Jsonb({"scanners": result.scanners, "reason": result.reason}),
            ),
        )
