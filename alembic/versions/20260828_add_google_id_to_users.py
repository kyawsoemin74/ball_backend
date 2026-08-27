"""officially manage users.google_id

Revision ID: 20260828_google_id
Revises: 20260825_lineup_finalization
Create Date: 2026-08-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260828_google_id"
down_revision = "20260825_lineup_finalization"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}

    if "google_id" not in user_columns:
        op.add_column("users", sa.Column("google_id", sa.String(length=255), nullable=True))

    constraint_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints("users")
    }
    if "uq_users_google_id" not in constraint_names:
        op.create_unique_constraint("uq_users_google_id", "users", ["google_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraint_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints("users")
    }

    if "uq_users_google_id" in constraint_names:
        op.drop_constraint("uq_users_google_id", "users", type_="unique")

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "google_id" in user_columns:
        op.drop_column("users", "google_id")