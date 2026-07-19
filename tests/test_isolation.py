"""Room isolation regression tests — the Phase 2 DoD (OWASP LLM08/LLM02).

Each test is a security guard: it actively tries to cross a room boundary and
asserts denial. The strongest ones run at the raw DB layer (independent of app
code), proving RLS itself holds; the API-layer ones prove the endpoints wire it
up correctly and leak nothing (404, not 403).

These need a Postgres with migration 0002 applied and the `app_rt` role. If the
DB isn't reachable (e.g. a bare `pytest` with no stack up) the whole module
skips, so it never turns into a false green — CI runs it against a real DB.

No embedding model or Claude key required: fixtures insert dummy vectors and the
denial paths return before any model/LLM call.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.errors import InsufficientPrivilege

from app.config import settings
from app.db import get_conn, session_for_user
from app.main import app

# A unit-norm vector (not all-zero: a zero vector has undefined cosine and can
# upset the HNSW cosine index on insert).
DIM = settings.embedding_dim
SAMPLE_VEC = [1.0] + [0.0] * (DIM - 1)


def _db_ready() -> bool:
    try:
        conn = psycopg.connect(settings.database_url, connect_timeout=2)
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.memberships')")
            return cur.fetchone()[0] is not None
    finally:
        conn.close()


pytestmark = pytest.mark.skipif(
    not _db_ready(), reason="Postgres with migration 0002 not reachable"
)

client = TestClient(app)


# --- Fixtures / helpers (admin bypasses RLS — used to build cross-room state) ---


@pytest.fixture
def admin():
    """Clean, RLS-bypassing connection; truncates state so each test is isolated."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE users, rooms, memberships, documents, chunks RESTART IDENTITY CASCADE"
            )
        conn.commit()
        yield conn
    finally:
        conn.rollback()
        conn.close()


def make_user(admin: psycopg.Connection, email: str) -> int:
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, 'x') RETURNING id",
            (email,),
        )
        uid = cur.fetchone()[0]
    admin.commit()
    return uid


def make_room(admin: psycopg.Connection, name: str, owner_id: int, members=()) -> int:
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO rooms (name, owner_user_id) VALUES (%s, %s) RETURNING id",
            (name, owner_id),
        )
        room_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO memberships (user_id, room_id, role) VALUES (%s, %s, 'owner')",
            (owner_id, room_id),
        )
        for m in members:
            cur.execute(
                "INSERT INTO memberships (user_id, room_id, role) VALUES (%s, %s, 'member')",
                (m, room_id),
            )
    admin.commit()
    return room_id


def seed_chunk(admin: psycopg.Connection, room_id: int, content: str = "TOP SECRET") -> None:
    with admin.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (title, source, room_id) VALUES ('doc', 'src', %s) RETURNING id",
            (room_id,),
        )
        doc_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO chunks (document_id, chunk_index, content, embedding, room_id) "
            "VALUES (%s, 0, %s, %s, %s)",
            (doc_id, content, SAMPLE_VEC, room_id),
        )
    admin.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_login(email: str, password: str = "pw-123456") -> str:
    client.post("/auth/register", json={"email": email, "password": password})
    resp = client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["token"]


# --- DB-layer guards (RLS itself) ----------------------------------------------


def test_nonmember_cannot_read_room_data(admin):
    alice = make_user(admin, "alice@db")
    bob = make_user(admin, "bob@db")
    room_a = make_room(admin, "alice-room", alice)
    seed_chunk(admin, room_a)

    with session_for_user(bob) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE room_id = %s", (room_a,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM documents WHERE room_id = %s", (room_a,))
        assert cur.fetchone()[0] == 0


def test_deny_by_default_when_identity_unset(admin):
    alice = make_user(admin, "alice@db")
    room_a = make_room(admin, "alice-room", alice)
    seed_chunk(admin, room_a)

    # Raw app_rt connection, no app.user_id set → must see nothing (fail closed).
    conn = psycopg.connect(settings.runtime_database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_nonmember_cannot_write_into_room(admin):
    alice = make_user(admin, "alice@db")
    bob = make_user(admin, "bob@db")
    room_a = make_room(admin, "alice-room", alice)

    with pytest.raises(InsufficientPrivilege):
        with session_for_user(bob) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (title, source, room_id) VALUES ('x', 'y', %s)",
                (room_a,),
            )


def test_member_can_write_and_read_own_room(admin):
    alice = make_user(admin, "alice@db")
    room_a = make_room(admin, "alice-room", alice)

    with session_for_user(alice) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO documents (title, source, room_id) VALUES ('x', 'y', %s) RETURNING id",
            (room_a,),
        )
        doc_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO chunks (document_id, chunk_index, content, embedding, room_id) "
            "VALUES (%s, 0, 'hi', %s, %s)",
            (doc_id, SAMPLE_VEC, room_a),
        )

    with session_for_user(alice) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE room_id = %s", (room_a,))
        assert cur.fetchone()[0] == 1


def test_nonmember_cannot_self_enrol(admin):
    """Regression for the 0003 fix: a user must not add THEMSELVES to a room they
    don't own, then read its data. The 0002 `memberships_insert` policy allowed
    self-insert into any room_id (privilege escalation); 0003 restricts inserts to
    rooms the caller owns."""
    alice = make_user(admin, "alice@db")
    mallory = make_user(admin, "mallory@db")
    room_a = make_room(admin, "alice-private", alice)
    seed_chunk(admin, room_a, content="alice secret")

    with pytest.raises(InsufficientPrivilege):
        with session_for_user(mallory) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memberships (user_id, room_id, role) VALUES (%s, %s, 'member')",
                (mallory, room_a),
            )

    # And the boundary still holds: Mallory sees nothing.
    with session_for_user(mallory) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE room_id = %s", (room_a,))
        assert cur.fetchone()[0] == 0


def test_owner_bootstrap_still_works_after_0003(admin):
    """The tightened policy must not break the create-room path: creating a room
    and enrolling yourself as its owner (via the RLS-scoped connection) still
    succeeds, because branch (B) sees the just-created owned room in-transaction."""
    alice = make_user(admin, "alice@db")
    from app.rooms import create_room

    with session_for_user(alice) as conn:
        room_id = create_room(conn, "alice-room", alice)

    with session_for_user(alice) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM memberships WHERE room_id = %s", (room_id,))
        assert cur.fetchone()[0] == 1


def test_shared_room_visible_to_all_members(admin):
    alice = make_user(admin, "alice@db")
    bob = make_user(admin, "bob@db")
    room = make_room(admin, "shared", alice, members=[bob])
    seed_chunk(admin, room, content="shared secret")

    for uid in (alice, bob):
        with session_for_user(uid) as conn, conn.cursor() as cur:
            cur.execute("SELECT content FROM chunks WHERE room_id = %s", (room,))
            rows = cur.fetchall()
        assert [r[0] for r in rows] == ["shared secret"]


# --- API-layer guards (endpoints wire RLS up + leak nothing) -------------------


def test_chat_requires_auth():
    resp = client.post("/chat", json={"question": "hi", "room_id": 1})
    assert resp.status_code in (401, 403)  # missing bearer


def test_api_nonmember_gets_404_not_403(admin):
    owner_token = _register_login("owner@api")
    intruder_token = _register_login("intruder@api")

    room_id = client.post("/rooms", json={"name": "private"}, headers=_auth(owner_token)).json()[
        "room_id"
    ]

    # A non-member must not be able to tell the room exists → 404 on read and write.
    chat = client.post(
        "/chat", json={"question": "secrets?", "room_id": room_id}, headers=_auth(intruder_token)
    )
    assert chat.status_code == 404

    upload = client.post(
        f"/rooms/{room_id}/documents",
        json={"title": "t", "content": "c"},
        headers=_auth(intruder_token),
    )
    assert upload.status_code == 404


def test_api_bootstrap_and_owner_only_membership(admin):
    owner_token = _register_login("owner@api")
    client.post("/auth/register", json={"email": "member@api", "password": "pw-123456"})
    client.post("/auth/register", json={"email": "third@api", "password": "pw-123456"})

    created = client.post("/rooms", json={"name": "team"}, headers=_auth(owner_token))
    assert created.status_code == 201
    room_id = created.json()["room_id"]

    # Owner adds a member (bootstrap membership path works under RLS).
    added = client.post(
        f"/rooms/{room_id}/members", json={"email": "member@api"}, headers=_auth(owner_token)
    )
    assert added.status_code == 201

    # A non-owner member cannot add others.
    member_token = _register_login("member@api")
    forbidden = client.post(
        f"/rooms/{room_id}/members", json={"email": "third@api"}, headers=_auth(member_token)
    )
    assert forbidden.status_code == 403
