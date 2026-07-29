"""audit_log: admit the scope-aware "flag" verdict

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-29

Phase 3 finding #2 (ADR-005). The scope-aware output policy records PII it
detected but deliberately allowed as verdict "flag" (advisory) — distinct from
"allow" (nothing tripped) and "block" (refused: canary/LLM07 or fail-closed).
Widen ck_audit_verdict, which 0004 pinned to ('allow','block'), to admit it.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_CK = "ck_audit_verdict"


def upgrade() -> None:
    op.drop_constraint(_CK, "audit_log", type_="check")
    op.create_check_constraint(_CK, "audit_log", "verdict IN ('allow', 'block', 'flag')")


def downgrade() -> None:
    op.drop_constraint(_CK, "audit_log", type_="check")
    op.create_check_constraint(_CK, "audit_log", "verdict IN ('allow', 'block')")
