from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "c8d7d9a4f2e1"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "countries",
        sa.Column("country_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=True),
        sa.Column("flag", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("country_id"),
    )
    op.create_index(op.f("ix_countries_country_id"), "countries", ["country_id"], unique=False)
    op.create_index(op.f("ix_countries_name"), "countries", ["name"], unique=True)


def downgrade():
    op.drop_index(op.f("ix_countries_name"), table_name="countries")
    op.drop_index(op.f("ix_countries_country_id"), table_name="countries")
    op.drop_table("countries")
