from pathlib import Path


RECONCILIATION = Path(__file__).parents[1] / "alembic" / "versions" / "9aa1b2c3d4e5_reconcile_legacy_player_ids.py"
FOREIGN_KEYS = Path(__file__).parents[1] / "alembic" / "versions" / "9b2c3d4e5f6a_add_player_foreign_keys_to_match_events.py"


def test_player_event_backfill_precedes_foreign_key_creation():
    reconciliation = RECONCILIATION.read_text(encoding="utf-8")
    foreign_keys = FOREIGN_KEYS.read_text(encoding="utf-8")

    assert "down_revision = \"9a1b2c3d4e5f\"" in reconciliation
    assert "down_revision = \"9aa1b2c3d4e5\"" in foreign_keys
    backfill = reconciliation.index("INSERT INTO players")
    player_remap = reconciliation.index("SET player_id = players.player_id")
    assist_remap = reconciliation.index("SET assist_id = players.player_id")
    orphan_validation = reconciliation.index("unresolved match_events.player_id references remain")
    player_fk = foreign_keys.index('"fk_match_events_player_id_players"')
    assist_fk = foreign_keys.index('"fk_match_events_assist_id_players"')

    assert backfill < player_remap < assist_remap < orphan_validation
    assert "provider_player_id = player_id::text" in reconciliation
    assert "provider_assist_id = assist_id::text" in reconciliation
    assert "ORDER BY count(*) DESC, player_name ASC" in reconciliation
    assert "ON CONFLICT (provider, provider_id) DO NOTHING" in reconciliation
