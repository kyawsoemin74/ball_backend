"""create match_statistics table

Revision ID: 8b3f2d4c1a7e
Revises: f4a5b6c7d8e9
Create Date: 2026-07-07 00:00:00.000000+00:00

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b3f2d4c1a7e'
down_revision = 'f4a5b6c7d8e9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'match_statistics',
        sa.Column('match_id', sa.Integer(), nullable=False),
        sa.Column('data', sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['match_id'], ['matches.fixture_id'], name=op.f('fk_match_statistics_match_id_matches')),
        sa.PrimaryKeyConstraint('match_id', name=op.f('pk_match_statistics')),
    )


def downgrade():
    op.drop_constraint(op.f('fk_match_statistics_match_id_matches'), 'match_statistics', type_='foreignkey')
    op.drop_table('match_statistics')
