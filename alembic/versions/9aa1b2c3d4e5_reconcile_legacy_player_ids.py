"""reconcile legacy provider player IDs before event foreign keys

Revision ID: 9aa1b2c3d4e5
Revises: 9a1b2c3d4e5f
"""

from alembic import op


revision = "9aa1b2c3d4e5"
down_revision = "9a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE match_events
        SET provider_player_id = player_id::text
        WHERE player_id IS NOT NULL AND provider_player_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE match_events
        SET provider_assist_id = assist_id::text
        WHERE assist_id IS NOT NULL AND provider_assist_id IS NULL
        """
    )
    op.execute(
        """
        WITH identity_names AS (
            SELECT provider_player_id AS provider_id, NULLIF(trim(player_name), '') AS player_name
            FROM match_events
            WHERE provider_player_id IS NOT NULL
            UNION ALL
            SELECT provider_assist_id AS provider_id, NULLIF(trim(assist_name), '') AS player_name
            FROM match_events
            WHERE provider_assist_id IS NOT NULL
        ),
        ranked_names AS (
            SELECT provider_id, player_name,
                   row_number() OVER (
                       PARTITION BY provider_id
                       ORDER BY count(*) DESC, player_name ASC
                   ) AS name_rank
            FROM identity_names
            WHERE player_name IS NOT NULL
            GROUP BY provider_id, player_name
        )
        INSERT INTO players (provider, provider_id, name)
        SELECT 'api-football', provider_id, player_name
        FROM ranked_names
        WHERE name_rank = 1
        ON CONFLICT (provider, provider_id) DO NOTHING
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM match_events
                WHERE provider_player_id IS NOT NULL
                GROUP BY provider_player_id
                HAVING count(*) FILTER (WHERE NULLIF(trim(player_name), '') IS NOT NULL) = 0
            ) THEN
                RAISE EXCEPTION 'legacy player identity has no source-backed name';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM match_events
                WHERE provider_assist_id IS NOT NULL
                GROUP BY provider_assist_id
                HAVING count(*) FILTER (WHERE NULLIF(trim(assist_name), '') IS NOT NULL) = 0
            ) THEN
                RAISE EXCEPTION 'legacy assist identity has no source-backed name';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        UPDATE match_events AS events
        SET player_id = players.player_id
        FROM players
        WHERE players.provider = 'api-football'
          AND players.provider_id = events.provider_player_id
        """
    )
    op.execute(
        """
        UPDATE match_events AS events
        SET assist_id = players.player_id
        FROM players
        WHERE players.provider = 'api-football'
          AND players.provider_id = events.provider_assist_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM match_events AS events
                LEFT JOIN players ON players.player_id = events.player_id
                WHERE events.player_id IS NOT NULL AND players.player_id IS NULL
            ) THEN
                RAISE EXCEPTION 'unresolved match_events.player_id references remain';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM match_events AS events
                LEFT JOIN players ON players.player_id = events.assist_id
                WHERE events.assist_id IS NOT NULL AND players.player_id IS NULL
            ) THEN
                RAISE EXCEPTION 'unresolved match_events.assist_id references remain';
            END IF;
        END
        $$
        """
    )


def downgrade():
    op.execute(
        """
        UPDATE match_events AS events
        SET player_id = events.provider_player_id::integer
        WHERE provider_player_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE match_events AS events
        SET assist_id = events.provider_assist_id::integer
        WHERE provider_assist_id IS NOT NULL
        """
    )
