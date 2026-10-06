"""add auth sessions for current-session logout

Revision ID: 20261006_auth_sessions
Revises: 20261005_match_finalization
Create Date: 2026-10-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20261006_auth_sessions"
down_revision = "20261005_match_finalization"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_identity", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("sid", name=op.f("uq_auth_sessions_sid")),
    )
    op.create_index(op.f("ix_auth_sessions_sid"), "auth_sessions", ["sid"], unique=True)
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_auth_sessions_revoked_at"), "auth_sessions", ["revoked_at"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_auth_sessions_revoked_at"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_sid"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
