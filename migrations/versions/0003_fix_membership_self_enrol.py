"""tighten memberships_insert: no self-enrolment into arbitrary rooms

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19

Closes a privilege-escalation hole in the Phase 2 RLS policy set. The original
`memberships_insert` policy (0002) allowed either branch:

    WITH CHECK (
        user_id = <me>                                          -- (A) self-insert
        OR room_id IN (SELECT id FROM rooms WHERE owner_user_id = <me>)  -- (B) owner adds
    )

Branch (A) placed NO constraint on `room_id`, so any authenticated user could
insert their OWN membership row into ANY room by id, then read that room's data
through the membership-gated read policies. That defeats the DB-layer isolation
guarantee (OWASP LLM08 / broken access control) — the very thing Phase 2 claims
to enforce independently of application code.

Branch (A) is also redundant for the create-room bootstrap: inside the creating
transaction the just-inserted room is already visible via the rooms SELECT policy
(`owner_user_id = <me>`), so branch (B) alone admits the owner's own membership.
Phase 2 has no self-join endpoint, so restricting inserts to owner-added
memberships changes no supported behaviour.

The new policy keeps only branch (B). When public/invite-joinable rooms arrive,
add a scoped self-join branch then (e.g. `user_id = <me> AND <room is joinable>`),
never an unconstrained `user_id = <me>`.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Kept in lock-step with 0002's definitions.
UID = "NULLIF(current_setting('app.user_id', true), '')::int"

# Owner-only insert: you may add a membership row only to a room you own.
_INSERT_TIGHT = f"""
    CREATE POLICY memberships_insert ON memberships FOR INSERT
    WITH CHECK (room_id IN (SELECT id FROM rooms WHERE owner_user_id = {UID}))
"""

# The original 0002 policy — restored verbatim on downgrade.
_INSERT_ORIGINAL = f"""
    CREATE POLICY memberships_insert ON memberships FOR INSERT
    WITH CHECK (
        user_id = {UID}
        OR room_id IN (SELECT id FROM rooms WHERE owner_user_id = {UID})
    )
"""


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS memberships_insert ON memberships")
    op.execute(_INSERT_TIGHT)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS memberships_insert ON memberships")
    op.execute(_INSERT_ORIGINAL)
