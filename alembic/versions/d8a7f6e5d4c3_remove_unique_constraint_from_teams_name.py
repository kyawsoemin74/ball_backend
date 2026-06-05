from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = 'd8a7f6e5d4c3'
down_revision = 'bafc6e79386f'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    result = conn.execute(text("""
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'teams_name_key'
    """))

    if result.fetchone():
        op.drop_constraint('teams_name_key', 'teams', type_='unique')


def downgrade():
    op.create_unique_constraint('teams_name_key', 'teams', ['name'])
