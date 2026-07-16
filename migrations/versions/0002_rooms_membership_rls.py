"""rooms, membership & Row-Level Security (Phase 2 tenant isolation)

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-16

Turns the single-tenant RAG core into a room-isolated one. A *room* is a
shareable RAG space; users join rooms via *memberships* (many-to-many); the room
is the isolation unit. Isolation is enforced by Postgres Row-Level Security, and
crucially by having the request path run as a NON-OWNER role (`app_rt`) — because
a superuser or the table owner bypasses RLS entirely.

Security mapping: OWASP LLM08 (vector/embedding cross-tenant leakage),
LLM02 (sensitive info disclosure), broken access control.
"""

import os

import sqlalchemy as sa
from alembic import op
from psycopg import sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Deny-by-default identity: unset -> NULL (matches no rows); empty string is
# coerced to NULL too, so ''::int can never throw instead of denying.
UID = "NULLIF(current_setting('app.user_id', true), '')::int"
# A user's rooms. Reused by the document/chunk read+write policies.
MEMBER_ROOMS = f"(SELECT room_id FROM memberships WHERE user_id = {UID})"


def _ensure_app_rt_role() -> None:
    """Create (or re-key) the non-owner runtime role, password from the env.

    The password is NEVER hardcoded — the repo is public and gitleaks (in CI)
    would flag it. We read APP_RT_PASSWORD at migrate time and quote it safely
    via psycopg's Literal (utility statements like CREATE/ALTER ROLE cannot take
    bind parameters). Idempotent: roles are cluster-global, so a re-run must not
    fail if the role already exists.
    """
    password = os.environ.get("APP_RT_PASSWORD", "app_rt")
    raw = op.get_bind().connection.driver_connection  # underlying psycopg3 conn
    with raw.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'app_rt'")
        action = "ALTER ROLE" if cur.fetchone() else "CREATE ROLE"
        cur.execute(
            sql.SQL(
                "{action} app_rt WITH LOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOBYPASSRLS PASSWORD {pw}"
            ).format(action=sql.SQL(action), pw=sql.Literal(password))
        )


def upgrade() -> None:
    # --- Identity & room model -------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        # Nullable so the migration can backfill a system-owned `legacy` room
        # without inventing a fake user. User-created rooms always set an owner,
        # and the RLS INSERT check (owner_user_id = current user) rejects NULL,
        # so app_rt can never create an unowned room.
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "memberships",
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "room_id",
            sa.Integer,
            sa.ForeignKey("rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False, server_default="member"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "room_id"),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),
    )
    op.create_index("ix_memberships_room_id", "memberships", ["room_id"])

    # --- Room-scope the existing KB tables -------------------------------------
    # Add room_id nullable first, backfill, then enforce NOT NULL + FKs.
    op.add_column("documents", sa.Column("room_id", sa.Integer, nullable=True))
    op.add_column("chunks", sa.Column("room_id", sa.Integer, nullable=True))

    bind = op.get_bind()
    has_docs = bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM documents)")).scalar()
    if has_docs:
        # Park pre-existing single-tenant data in a system-owned legacy room so
        # the NOT NULL upgrade is non-breaking. It has no members -> invisible to
        # app_rt; operators re-ingest into real rooms.
        legacy_id = bind.execute(
            sa.text("INSERT INTO rooms (name, owner_user_id) VALUES ('legacy', NULL) RETURNING id")
        ).scalar()
        bind.execute(
            sa.text("UPDATE documents SET room_id = :rid WHERE room_id IS NULL"),
            {"rid": legacy_id},
        )
        # Keep each chunk's room in lock-step with its parent document.
        bind.execute(
            sa.text(
                "UPDATE chunks SET room_id = d.room_id "
                "FROM documents d WHERE d.id = chunks.document_id"
            )
        )

    op.alter_column("documents", "room_id", nullable=False)
    op.alter_column("chunks", "room_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_room_id", "documents", "rooms", ["room_id"], ["id"], ondelete="CASCADE"
    )
    # Composite-FK target: guarantees a chunk's room matches its document's room.
    # FK checks run as the table owner and bypass RLS, so this constraint — not
    # application code — is the real integrity guard against a cross-room chunk.
    op.create_unique_constraint("uq_documents_id_room", "documents", ["id", "room_id"])
    op.create_foreign_key(
        "fk_chunks_document_room",
        "chunks",
        "documents",
        ["document_id", "room_id"],
        ["id", "room_id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_documents_room_id", "documents", ["room_id"])
    op.create_index("ix_chunks_room_id", "chunks", ["room_id"])

    # --- Runtime role ----------------------------------------------------------
    _ensure_app_rt_role()

    # --- Row-Level Security ----------------------------------------------------
    # ENABLE (not FORCE): app_rt is neither superuser nor owner, so policies are
    # enforced for it; `app` (owner) still bypasses for migrations/seed.
    for table in ("rooms", "memberships", "documents", "chunks"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    # rooms: an owner always sees their room (required so the memberships-insert
    # subquery below resolves before any membership row exists); members see theirs.
    op.execute(
        f"""
        CREATE POLICY rooms_select ON rooms FOR SELECT
        USING (owner_user_id = {UID} OR id IN {MEMBER_ROOMS})
        """
    )
    op.execute(f"CREATE POLICY rooms_insert ON rooms FOR INSERT WITH CHECK (owner_user_id = {UID})")

    # memberships: you see only your own rows. Insert allows (a) adding your OWN
    # membership — the bootstrap path, independent of room visibility — or (b) an
    # owner adding others to a room they own.
    op.execute(
        f"CREATE POLICY memberships_select ON memberships FOR SELECT USING (user_id = {UID})"
    )
    op.execute(
        f"""
        CREATE POLICY memberships_insert ON memberships FOR INSERT
        WITH CHECK (
            user_id = {UID}
            OR room_id IN (SELECT id FROM rooms WHERE owner_user_id = {UID})
        )
        """
    )

    # documents / chunks: read AND write gated by membership. WITH CHECK on the
    # write side is what stops a member injecting data into a room they aren't in.
    for table in ("documents", "chunks"):
        op.execute(
            f"""
            CREATE POLICY {table}_rw ON {table} FOR ALL
            USING (room_id IN {MEMBER_ROOMS})
            WITH CHECK (room_id IN {MEMBER_ROOMS})
            """
        )

    # --- Grants for the runtime role ------------------------------------------
    op.execute("GRANT USAGE ON SCHEMA public TO app_rt")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON documents, chunks TO app_rt")
    op.execute("GRANT SELECT, INSERT ON rooms, memberships TO app_rt")
    op.execute("GRANT SELECT ON users TO app_rt")  # auth needs email lookup
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rt")


def downgrade() -> None:
    for table in ("documents", "chunks"):
        op.execute(f"DROP POLICY IF EXISTS {table}_rw ON {table}")
    op.execute("DROP POLICY IF EXISTS memberships_insert ON memberships")
    op.execute("DROP POLICY IF EXISTS memberships_select ON memberships")
    op.execute("DROP POLICY IF EXISTS rooms_insert ON rooms")
    op.execute("DROP POLICY IF EXISTS rooms_select ON rooms")
    for table in ("rooms", "memberships", "documents", "chunks"):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_chunks_room_id", table_name="chunks")
    op.drop_index("ix_documents_room_id", table_name="documents")
    op.drop_constraint("fk_chunks_document_room", "chunks", type_="foreignkey")
    op.drop_constraint("uq_documents_id_room", "documents", type_="unique")
    op.drop_constraint("fk_documents_room_id", "documents", type_="foreignkey")
    op.drop_column("chunks", "room_id")
    op.drop_column("documents", "room_id")

    op.drop_index("ix_memberships_room_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("rooms")
    op.drop_table("users")

    # Remove the role and every grant that depends on it. Guarded so a partial
    # downgrade doesn't error if the role is already gone.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rt') THEN
                DROP OWNED BY app_rt;
                DROP ROLE app_rt;
            END IF;
        END
        $$
        """
    )
