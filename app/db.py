"""Database connections.

Two connection paths, split by trust (this split is the crux of Phase 2 RLS):

- `get_conn()` — the **admin** role `app`. Superuser + table owner, so it
  BYPASSES Row-Level Security. Used only for migrations and operator ingest/seed.
  Never put it on a user request path.
- `session_for_user()` — the **runtime** role `app_rt`. Non-owner, non-superuser,
  so RLS is enforced for it. Every user request goes through here, inside a
  transaction that carries the caller's identity as `app.user_id`.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings


def get_conn() -> psycopg.Connection:
    """Admin connection (bypasses RLS). Migrations + operator ingest only."""
    conn = psycopg.connect(settings.database_url)
    register_vector(conn)
    return conn


@contextmanager
def session_for_user(user_id: int) -> Iterator[psycopg.Connection]:
    """Yield an `app_rt` connection scoped to `user_id` for one transaction.

    Sets `app.user_id` transaction-locally (`set_config(..., is_local=true)`) so
    RLS policies resolve the caller's rooms and the value can't leak onto a later
    request even if the connection were ever reused. The connection must NOT be
    in autocommit: `SET LOCAL` outside a transaction silently no-ops, which would
    run RLS "open". psycopg opens a transaction implicitly on first execute, so
    we simply never enable autocommit here. Deny-by-default still holds: with no
    `app.user_id` set the policies match no rows.
    """
    conn = psycopg.connect(settings.runtime_database_url)  # autocommit=False by default
    try:
        register_vector(conn)
        with conn.cursor() as cur:
            # Parameterized so the id can't be injected; text value, coerced back
            # to int by the policies via NULLIF(...)::int.
            cur.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
