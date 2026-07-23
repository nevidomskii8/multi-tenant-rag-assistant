"""PII records (tickets/orders/customer_profiles) + audit_log, all under RLS

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-22

Phase 3 data model. Adds the private records a support tenant actually holds —
`tickets`, `orders`, `customer_profiles` (synthetic PII) — plus an append-only
`audit_log` for guardrail decisions. Every table reuses the Phase 2 isolation
pattern verbatim: room-scoped rows, RLS ENABLEd, policies keyed on the caller's
membership, DML granted to the non-owner runtime role `app_rt` (so RLS bites).
Schema only — the PII tables carry a `room_id`, and no rooms exist at migrate
time, so the synthetic seed lives in a runtime seed step, not here (same reason
`0001` doesn't seed the KB).

Security mapping: OWASP LLM02 (sensitive-info disclosure) — real PII now flows
through the RAG path and cross-room isolation (LLM08) protects it directly;
`audit_log` gives LLM02/LLM07 decisions a durable, room-scoped, testable home.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Kept in lock-step with 0002's definitions (deny-by-default identity + a
# caller's rooms). Reused by every policy below.
UID = "NULLIF(current_setting('app.user_id', true), '')::int"
MEMBER_ROOMS = f"(SELECT room_id FROM memberships WHERE user_id = {UID})"

# The PII source tables: full read+write gated by membership, like documents/chunks.
_PII_TABLES = ("tickets", "orders", "customer_profiles")


def _room_id_column() -> sa.Column:
    """A NOT NULL room_id FK — the isolation key every Phase 3 table shares."""
    return sa.Column(
        "room_id",
        sa.Integer,
        sa.ForeignKey("rooms.id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    # --- PII source records ----------------------------------------------------
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer, primary_key=True),
        _room_id_column(),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("customer_name", sa.Text, nullable=True),
        sa.Column("customer_email", sa.Text, nullable=True),
        _created_at_column(),
        sa.CheckConstraint("status IN ('open', 'pending', 'closed')", name="ck_tickets_status"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True),
        _room_id_column(),
        sa.Column("customer_email", sa.Text, nullable=False),
        sa.Column("item", sa.Text, nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="placed"),
        _created_at_column(),
        sa.CheckConstraint(
            "status IN ('placed', 'shipped', 'delivered', 'cancelled')",
            name="ck_orders_status",
        ),
    )
    op.create_table(
        "customer_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        _room_id_column(),
        sa.Column("full_name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        sa.Column("phone", sa.Text, nullable=True),
        sa.Column("address", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        _created_at_column(),
    )

    # --- Audit log (append-only) -----------------------------------------------
    # One row per guardrail decision. `detail` holds scanner names/scores/verdict
    # only — never the raw PII the guard just redacted.
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _room_id_column(),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("verdict", sa.Text, nullable=False),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at_column(),
        sa.CheckConstraint(
            "event_type IN ('input_scan', 'output_scan')", name="ck_audit_event_type"
        ),
        sa.CheckConstraint("verdict IN ('allow', 'block')", name="ck_audit_verdict"),
    )

    for table in (*_PII_TABLES, "audit_log"):
        op.create_index(f"ix_{table}_room_id", table, ["room_id"])

    # --- Row-Level Security ----------------------------------------------------
    # ENABLE (not FORCE): app_rt is non-owner/non-superuser so policies bite for
    # it; `app` (owner) still bypasses for migrations + operator seed.
    for table in (*_PII_TABLES, "audit_log"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

    # PII tables: read AND write gated by membership, identical to documents/chunks.
    for table in _PII_TABLES:
        op.execute(
            f"""
            CREATE POLICY {table}_rw ON {table} FOR ALL
            USING (room_id IN {MEMBER_ROOMS})
            WITH CHECK (room_id IN {MEMBER_ROOMS})
            """
        )

    # audit_log: a caller reads only their own rooms' events and may only append
    # rows into a room they belong to. Append-only is enforced by the grants
    # below (SELECT, INSERT — no UPDATE/DELETE), not by the policy.
    op.execute(
        f"""
        CREATE POLICY audit_log_rw ON audit_log FOR ALL
        USING (room_id IN {MEMBER_ROOMS})
        WITH CHECK (room_id IN {MEMBER_ROOMS})
        """
    )

    # --- Grants for the runtime role -------------------------------------------
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON tickets, orders, customer_profiles TO app_rt"
    )
    op.execute("GRANT SELECT, INSERT ON audit_log TO app_rt")  # append-only
    # New serial/identity sequences created above aren't covered by 0002's grant.
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_rt")


def downgrade() -> None:
    for table in (*_PII_TABLES, "audit_log"):
        op.execute(f"DROP POLICY IF EXISTS {table}_rw ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("audit_log")
    op.drop_table("customer_profiles")
    op.drop_table("orders")
    op.drop_table("tickets")
