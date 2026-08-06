"""add user profile fields

Revision ID: 5f6a7b8c9d0e
Revises: 7f2d4c1e9a6b
Create Date: 2026-08-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "5f6a7b8c9d0e"
down_revision = "7f2d4c1e9a6b"
branch_labels = None
depends_on = None


def upgrade():
    avatar_source_enum = postgresql.ENUM("default", "google", "upload", name="avatarsource")
    avatar_source_enum.create(op.get_bind(), checkfirst=True)

    op.add_column("users", sa.Column("display_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(length=2048), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "avatar_source",
            avatar_source_enum,
            nullable=False,
            server_default=sa.text("'default'"),
        ),
    )


def downgrade():
    op.drop_column("users", "avatar_source")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "display_name")

    avatar_source_enum = postgresql.ENUM("default", "google", "upload", name="avatarsource")
    avatar_source_enum.drop(op.get_bind(), checkfirst=True)
