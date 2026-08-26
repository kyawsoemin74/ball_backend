"""create referees master table

Revision ID: dfe1b2c3a4e5
Revises: a8c9d0e1f2g3
Create Date: 2026-08-16 00:00:00.000000+00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "dfe1b2c3a4e5"
down_revision = "a8c9d0e1f2g3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referees",
        sa.Column("referee_id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("provider", sa.String(length=50), server_default="api-football", nullable=False),
        sa.Column("provider_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("nationality", sa.String(length=100), nullable=True),
        sa.Column("photo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("referee_id"),
        sa.UniqueConstraint("provider", "normalized_name", name="uq_referees_provider_normalized_name"),
        sa.UniqueConstraint("provider", "provider_id", name="uq_referees_provider_provider_id"),
    )

    op.create_index("ix_referees_provider_id", "referees", ["provider_id"], unique=False)
    op.create_index("ix_referees_name", "referees", ["name"], unique=False)
    op.create_index("ix_referees_normalized_name", "referees", ["normalized_name"], unique=False)
    op.create_index("ix_referees_referee_id", "referees", ["referee_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_referees_referee_id", table_name="referees")
    op.drop_index("ix_referees_normalized_name", table_name="referees")
    op.drop_index("ix_referees_name", table_name="referees")
    op.drop_index("ix_referees_provider_id", table_name="referees")
    op.drop_table("referees")
