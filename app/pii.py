"""Synthetic PII records: seed the relational tables and fold them into the RAG path.

Phase 3 gives a room real private records — `tickets`, `orders`,
`customer_profiles` (all synthetic; never real people). Two things happen per
record:

1. it is inserted into its room-scoped table (the structured source of truth), and
2. it is rendered to text and ingested into `chunks` (e5 `passage:` embeddings)
   with its `room_id`, so `/chat` can surface it and the guardrail redaction
   (Anonymize on input, Sensitive on output) is exercised on genuine PII — the
   whole point of embedding PII into RAG (ADR-005).

Retrieval is unchanged: the chunks carry `room_id`, so Phase 2 RLS already scopes
them to members. This is an OPERATOR seed — it runs on the admin `app` connection
(bypasses RLS), the same trust model as the KB ingest CLI:

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app \\
        python -m app.pii --room <room_id> [--force]
"""

import argparse
import json
from pathlib import Path

import psycopg

from app.db import get_conn
from app.ingest import ingest_text

PII_DIR = Path("data/pii")

# Stable `documents.source` prefix for a rendered record, so re-ingest is
# idempotent (ingest_text deletes the prior version at the same source) and a
# force re-seed can find and drop exactly the rows it wrote.
_SOURCE_PREFIX = "pii"


def render_ticket(row: dict) -> str:
    return (
        f"Support ticket (status: {row['status']})\n"
        f"Customer: {row['customer_name']} <{row['customer_email']}>\n"
        f"Subject: {row['subject']}\n\n"
        f"{row['body']}"
    )


def render_order(row: dict) -> str:
    amount = f"${row['amount_cents'] / 100:.2f}"
    return (
        f"Order (status: {row['status']})\n"
        f"Customer: {row['customer_email']}\n"
        f"Item: {row['item']}\n"
        f"Amount: {amount}"
    )


def render_profile(row: dict) -> str:
    return (
        f"Customer profile: {row['full_name']}\n"
        f"Email: {row['email']}\n"
        f"Phone: {row['phone']}\n"
        f"Address: {row['address']}\n"
        f"Notes: {row['notes']}"
    )


# (table, INSERT columns, text renderer, document-title fn). Fixture file is
# data/pii/<table>.json.
_RECORD_TYPES = (
    (
        "tickets",
        ("subject", "body", "status", "customer_name", "customer_email"),
        render_ticket,
        lambda r: f"Ticket: {r['subject']}",
    ),
    (
        "orders",
        ("customer_email", "item", "amount_cents", "status"),
        render_order,
        lambda r: f"Order: {r['item']}",
    ),
    (
        "customer_profiles",
        ("full_name", "email", "phone", "address", "notes"),
        render_profile,
        lambda r: f"Profile: {r['full_name']}",
    ),
)


def _load_fixture(table: str) -> list[dict]:
    return json.loads((PII_DIR / f"{table}.json").read_text(encoding="utf-8"))


def _already_seeded(conn: psycopg.Connection, room_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM tickets WHERE room_id = %s LIMIT 1", (room_id,))
        return cur.fetchone() is not None


def _wipe(conn: psycopg.Connection, room_id: int) -> None:
    """Drop this room's seeded records + their rendered documents (chunks cascade)."""
    with conn.cursor() as cur:
        for table, *_ in _RECORD_TYPES:
            cur.execute(f"DELETE FROM {table} WHERE room_id = %s", (room_id,))
        cur.execute(
            "DELETE FROM documents WHERE room_id = %s AND source LIKE %s",
            (room_id, f"{_SOURCE_PREFIX}/%"),
        )


def seed_room(conn: psycopg.Connection, room_id: int, *, force: bool = False) -> dict[str, int]:
    """Insert synthetic PII for a room and ingest each record into `chunks`.

    Idempotent: a no-op if the room is already seeded unless `force`, which first
    wipes this room's seeded rows + rendered documents. Does not commit — the
    caller owns the transaction (the CLI commits once at the end).
    """
    if _already_seeded(conn, room_id):
        if not force:
            return {}
        _wipe(conn, room_id)

    counts: dict[str, int] = {}
    for table, columns, render, title_of in _RECORD_TYPES:
        rows = _load_fixture(table)
        placeholders = ", ".join(["%s"] * (len(columns) + 1))  # +1 for room_id
        col_list = ", ".join(("room_id", *columns))
        n_chunks = 0
        with conn.cursor() as cur:
            for rec in rows:
                cur.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) RETURNING id",
                    (room_id, *[rec[c] for c in columns]),
                )
                record_id = cur.fetchone()[0]
                # Render from the fixture (identical to the row just inserted) and
                # ingest with a stable per-record source key.
                source = f"{_SOURCE_PREFIX}/{table}/{record_id}"
                n_chunks += ingest_text(conn, title_of(rec), render(rec), room_id, source=source)
        counts[table] = len(rows)
        counts[f"{table}_chunks"] = n_chunks
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed synthetic PII records into a room and fold them into RAG (operator)."
    )
    parser.add_argument("--room", type=int, required=True, help="target room id")
    parser.add_argument(
        "--force", action="store_true", help="re-seed: wipe this room's seeded PII first"
    )
    args = parser.parse_args()

    with get_conn() as conn:  # admin: seeding is a privileged operation
        counts = seed_room(conn, args.room, force=args.force)
        conn.commit()

    if not counts:
        print(f"Room {args.room} already seeded — pass --force to re-seed.")
        return
    for table, *_ in _RECORD_TYPES:
        print(f"  {table}: {counts[table]} records, {counts[f'{table}_chunks']} chunks")
    print(f"Seeded synthetic PII into room {args.room}.")


if __name__ == "__main__":
    main()
