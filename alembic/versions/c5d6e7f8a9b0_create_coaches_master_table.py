"""create coaches master table

Revision ID: c5d6e7f8a9b0
Revises: dfe1b2c3a4e5
Create Date: 2026-08-16 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c5d6e7f8a9b0"
down_revision = "dfe1b2c3a4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "coaches",
        sa.Column("coach_id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("provider", sa.String(length=50), server_default="api-football", nullable=False),
        sa.Column("provider_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("nationality", sa.String(length=100), nullable=True),
        sa.Column("photo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("coach_id"),
        sa.UniqueConstraint("provider", "normalized_name", name="uq_coaches_provider_normalized_name"),
        sa.UniqueConstraint("provider", "provider_id", name="uq_coaches_provider_provider_id"),
    )

    op.create_index("ix_coaches_provider_id", "coaches", ["provider_id"], unique=False)
    op.create_index("ix_coaches_name", "coaches", ["name"], unique=False)
    op.create_index("ix_coaches_normalized_name", "coaches", ["normalized_name"], unique=False)
    op.create_index("ix_coaches_coach_id", "coaches", ["coach_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_coaches_coach_id", table_name="coaches")
    op.drop_index("ix_coaches_normalized_name", table_name="coaches")
    op.drop_index("ix_coaches_name", table_name="coaches")
    op.drop_index("ix_coaches_provider_id", table_name="coaches")
    op.drop_table("coaches")
