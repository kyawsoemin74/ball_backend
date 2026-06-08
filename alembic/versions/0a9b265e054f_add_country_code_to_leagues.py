from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0a9b265e054f"
down_revision = "f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "leagues",
        sa.Column("country_code", sa.String(length=20), nullable=True)
    )


def downgrade():
    op.drop_column("leagues", "country_code")