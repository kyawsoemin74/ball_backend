"""repair Odds foreign key to canonical local Match identity

Revision ID: 20260911_repair_odds_match_fk
Revises: 20260910_match_identity_sequence
"""

from alembic import op
import sqlalchemy as sa


revision = "20260911_repair_odds_match_fk"
down_revision = "20260910_match_identity_sequence"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "fk_odds_fixture_id_matches_local_match_id"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    odds_foreign_keys = inspector.get_foreign_keys("odds")
    existing_fixture_fks = [
        foreign_key
        for foreign_key in odds_foreign_keys
        if foreign_key.get("constrained_columns") == ["fixture_id"]
    ]
    if existing_fixture_fks:
        raise RuntimeError(
            "odds.fixture_id already has a foreign key; refusing to alter an existing relationship"
        )

    orphan_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM odds AS o
            LEFT JOIN matches AS m ON m.local_match_id = o.fixture_id
            WHERE m.local_match_id IS NULL
            """
        )
    ).scalar_one()
    if orphan_count:
        examples = bind.execute(
            sa.text(
                """
                SELECT o.fixture_id
                FROM odds AS o
                LEFT JOIN matches AS m ON m.local_match_id = o.fixture_id
                WHERE m.local_match_id IS NULL
                ORDER BY o.fixture_id
                LIMIT 10
                """
            )
        ).scalars().all()
        raise RuntimeError(
            f"cannot create {CONSTRAINT_NAME}: {orphan_count} orphan Odds rows; examples={examples}"
        )

    op.create_foreign_key(
        CONSTRAINT_NAME,
        "odds",
        "matches",
        ["fixture_id"],
        ["local_match_id"],
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if any(foreign_key.get("name") == CONSTRAINT_NAME for foreign_key in inspector.get_foreign_keys("odds")):
        op.drop_constraint(CONSTRAINT_NAME, "odds", type_="foreignkey")
