"""Room + membership operations, run on an RLS-scoped (`app_rt`) connection.

Every function here expects a connection opened via `session_for_user`, so the
policies do the access control: a caller can only create rooms they own, only
see/act on rooms they belong to, and only owners can add other members. The
functions stay pure DB (no HTTP) — the API layer maps their results to status
codes.
"""

import psycopg


def create_room(conn: psycopg.Connection, name: str, owner_user_id: int) -> int:
    """Create a room and enrol the creator as its owner, in one transaction.

    Order matters for the RLS bootstrap: inserting the owner's OWN membership
    passes the `user_id = current user` policy branch, so it does not depend on
    the freshly-created room being visible yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rooms (name, owner_user_id) VALUES (%s, %s) RETURNING id",
            (name, owner_user_id),
        )
        room_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO memberships (user_id, room_id, role) VALUES (%s, %s, 'owner')",
            (owner_user_id, room_id),
        )
    return room_id


def room_owner(conn: psycopg.Connection, room_id: int) -> int | None:
    """Owner id if the room is visible to the caller, else None (RLS-hidden)."""
    with conn.cursor() as cur:
        cur.execute("SELECT owner_user_id FROM rooms WHERE id = %s", (room_id,))
        row = cur.fetchone()
    return row[0] if row else None


def is_member(conn: psycopg.Connection, room_id: int) -> bool:
    """True iff the caller can access the room (owner or member).

    Relies on the rooms SELECT policy: a hidden room returns no row, so this is
    also the anti-enumeration check behind the API's 404s.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM rooms WHERE id = %s", (room_id,))
        return cur.fetchone() is not None


def add_member(conn: psycopg.Connection, room_id: int, user_id: int) -> None:
    """Add a user to a room as a plain member.

    Deliberately NOT `ON CONFLICT DO NOTHING`: under RLS, ON CONFLICT must read
    the (possibly) conflicting row to decide, and `memberships_select` only
    exposes the caller's OWN rows — so an owner inserting *another* user's row
    (which they can't SELECT) makes ON CONFLICT raise an RLS error. A plain
    INSERT has no such read; a duplicate surfaces as a UniqueViolation, which the
    API layer turns into an idempotent success.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO memberships (user_id, room_id, role) VALUES (%s, %s, 'member')",
            (user_id, room_id),
        )
