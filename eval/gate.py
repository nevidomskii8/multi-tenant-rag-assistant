"""Deterministic retrieval gate — the CI-safe eval (no LLM, no API key).

For every in-scope golden question, check that the expected source document is
present in the top-k retrieved chunks. This catches the common silent
regressions — broken ingest, wrong embedding model/dim, chunking or retriever
bugs — without calling Claude, so it is cheap, reproducible, and never flaky.

Since Phase 2, retrieval runs under RLS: the gate seeds the golden KB into a
dedicated eval room (as the admin/operator) and retrieves as a *member* of it
(via app_rt), so the gate also exercises the isolation path end-to-end.

    DATABASE_URL=postgresql://app:<pw>@localhost:5432/app \\
    RUNTIME_DATABASE_URL=postgresql://app_rt:<pw>@localhost:5432/app \\
        python -m eval.gate

Exits non-zero if hit-rate falls below the threshold, failing the CI build.
"""

import json
import sys
from pathlib import Path

from app.db import get_conn, session_for_user
from app.ingest import ingest_file
from app.retrieval import retrieve

GOLDEN = Path(__file__).parent / "golden.json"
KB_DIR = Path("data/kb")
EVAL_EMAIL = "eval@local"
EVAL_ROOM = "eval"
# With only a handful of chunks, k=4 returns every document, so "expected in
# top-k" is trivially true. We assert the stricter, meaningful thing: the
# expected document ranks FIRST. Raise K to "in top-k" once the KB grows.
K = 1
THRESHOLD = 1.0


def _seed_eval_room() -> tuple[int, int]:
    """Ensure an eval user + room + membership exist, and (re)ingest the KB.

    Idempotent so repeated local runs don't pile up rooms. Runs as admin — this
    is operator seeding, which legitimately bypasses RLS.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, 'x') "
                "ON CONFLICT (email) DO NOTHING",
                (EVAL_EMAIL,),
            )
            cur.execute("SELECT id FROM users WHERE email = %s", (EVAL_EMAIL,))
            user_id = cur.fetchone()[0]
            cur.execute(
                "SELECT id FROM rooms WHERE name = %s AND owner_user_id = %s",
                (EVAL_ROOM, user_id),
            )
            row = cur.fetchone()
            if row:
                room_id = row[0]
            else:
                cur.execute(
                    "INSERT INTO rooms (name, owner_user_id) VALUES (%s, %s) RETURNING id",
                    (EVAL_ROOM, user_id),
                )
                room_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO memberships (user_id, room_id, role) VALUES (%s, %s, 'owner')",
                    (user_id, room_id),
                )
        conn.commit()
        for path in sorted(KB_DIR.glob("*.md")):
            ingest_file(conn, path, room_id)
            conn.commit()
    return user_id, room_id


def main() -> None:
    user_id, room_id = _seed_eval_room()
    items = [x for x in json.loads(GOLDEN.read_text()) if x.get("expected_source")]
    hits = 0
    with session_for_user(user_id) as conn:  # retrieve as a member → through RLS
        for item in items:
            sources = {h["source"] for h in retrieve(conn, item["question"], K, room_id=room_id)}
            ok = item["expected_source"] in sources
            hits += ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {item['id']}: {item['expected_source']}")

    rate = hits / len(items) if items else 0.0
    print(f"\nRetrieval hit-rate: {hits}/{len(items)} = {rate:.0%} (threshold {THRESHOLD:.0%})")
    if rate < THRESHOLD:
        print("GATE FAILED", file=sys.stderr)
        sys.exit(1)
    print("GATE PASSED")


if __name__ == "__main__":
    main()
