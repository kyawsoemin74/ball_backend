from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "7d8f6a3b4c1d"
down_revision = "61d3320badab"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_h2h",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("h2h_key", sa.String(length=50), nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name="match_h2h_pkey"),
        sa.UniqueConstraint("h2h_key", name="match_h2h_h2h_key_key"),
    )

    op.create_index(
        "ix_match_h2h_h2h_key",
        "match_h2h",
        ["h2h_key"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_match_h2h_h2h_key",
        table_name="match_h2h",
    )

    op.drop_table("match_h2h")