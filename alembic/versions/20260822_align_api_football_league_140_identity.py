"""align API-Football League 140 with canonical local identity

Revision ID: 20260822_align_league_140
Revises: 9c3d4e5f6a7b
"""

from alembic import op
import sqlalchemy as sa


revision = "20260822_align_league_140"
down_revision = "9c3d4e5f6a7b"
branch_labels = None
depends_on = None

_SOURCE_LOCAL_ID = 1
_TARGET_LOCAL_ID = 140
_PROVIDER = "api-football"
_PROVIDER_ID = "140"


def _count(bind, query: str, **parameters) -> int:
    return int(bind.execute(sa.text(query), parameters).scalar_one())


def _assert_no_dependent_rows(bind, local_id: int) -> None:
    dependencies = {
        "allowed_leagues": "SELECT count(*) FROM allowed_leagues WHERE league_id = :local_id",
        "league_seasons": "SELECT count(*) FROM league_seasons WHERE league_id = :local_id",
        "matches": "SELECT count(*) FROM matches WHERE league_id = :local_id",
        "standings": "SELECT count(*) FROM standings WHERE league_id = :local_id",
        "teams.current_league_id": "SELECT count(*) FROM teams WHERE current_league_id = :local_id",
    }
    found = {
        table: count
        for table, query in dependencies.items()
        if (count := _count(bind, query, local_id=local_id))
    }
    if found:
        raise RuntimeError(
            f"Cannot align League 140: dependent rows reference local league_id={local_id}: {found}"
        )


def upgrade() -> None:
    bind = op.get_bind()

    source = bind.execute(
        sa.text(
            """
            SELECT league_id, provider, provider_id
            FROM leagues
            WHERE provider = :provider AND provider_id = :provider_id
            FOR UPDATE
            """
        ),
        {"provider": _PROVIDER, "provider_id": _PROVIDER_ID},
    ).mappings().all()
    if not source:
        return
    if len(source) > 1:
        raise RuntimeError(
            "Cannot align API-Football provider_id=140: duplicate provider identity rows found"
        )
    current_local_id = int(source[0]["league_id"])
    if current_local_id == _TARGET_LOCAL_ID:
        return
    if current_local_id != _SOURCE_LOCAL_ID:
        raise RuntimeError(
            "Cannot align API-Football provider_id=140: "
            f"current local league_id={current_local_id}; expected 1 or 140"
        )

    target_count = _count(bind, "SELECT count(*) FROM leagues WHERE league_id = :local_id", local_id=_TARGET_LOCAL_ID)
    if target_count:
        raise RuntimeError("Cannot align League 140: target local league_id=140 already exists")

    _assert_no_dependent_rows(bind, _SOURCE_LOCAL_ID)

    bind.execute(
        sa.text(
            """
            UPDATE leagues
            SET league_id = :target_id
            WHERE league_id = :source_id
              AND provider = :provider
              AND provider_id = :provider_id
            """
        ),
        {
            "target_id": _TARGET_LOCAL_ID,
            "source_id": _SOURCE_LOCAL_ID,
            "provider": _PROVIDER,
            "provider_id": _PROVIDER_ID,
        },
    )

    bind.execute(
        sa.text(
            "SELECT setval('public.leagues_league_id_seq', GREATEST(:target_id, COALESCE((SELECT max(league_id) FROM leagues), 0)), true)"
        ),
        {"target_id": _TARGET_LOCAL_ID},
    )

    if _count(bind, "SELECT count(*) FROM leagues WHERE league_id = :local_id AND provider = :provider AND provider_id = :provider_id", local_id=_TARGET_LOCAL_ID, provider=_PROVIDER, provider_id=_PROVIDER_ID) != 1:
        raise RuntimeError("League 140 identity alignment verification failed")


def downgrade() -> None:
    bind = op.get_bind()

    target = bind.execute(
        sa.text(
            """
            SELECT league_id, provider, provider_id
            FROM leagues
            WHERE league_id = :target_id
              AND provider = :provider
              AND provider_id = :provider_id
            FOR UPDATE
            """
        ),
        {"target_id": _TARGET_LOCAL_ID, "provider": _PROVIDER, "provider_id": _PROVIDER_ID},
    ).mappings().all()
    if len(target) != 1:
        raise RuntimeError("Expected aligned API-Football League 140 before downgrade")
    if _count(bind, "SELECT count(*) FROM leagues WHERE league_id = :local_id", local_id=_SOURCE_LOCAL_ID):
        raise RuntimeError("Cannot downgrade League 140: source local league_id=1 is occupied")

    _assert_no_dependent_rows(bind, _TARGET_LOCAL_ID)

    bind.execute(
        sa.text(
            """
            UPDATE leagues
            SET league_id = :source_id
            WHERE league_id = :target_id
              AND provider = :provider
              AND provider_id = :provider_id
            """
        ),
        {
            "source_id": _SOURCE_LOCAL_ID,
            "target_id": _TARGET_LOCAL_ID,
            "provider": _PROVIDER,
            "provider_id": _PROVIDER_ID,
        },
    )

    bind.execute(
        sa.text(
            "SELECT setval('public.leagues_league_id_seq', GREATEST(:minimum_id, COALESCE((SELECT max(league_id) FROM leagues), 0)), true)"
        ),
        {"minimum_id": _SOURCE_LOCAL_ID},
    )
