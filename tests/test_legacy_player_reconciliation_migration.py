from pathlib import Path


MIGRATION = Path(__file__).parents[1] / "alembic" / "versions" / "9aa1b2c3d4e5_reconcile_legacy_player_ids.py"
FOREIGN_KEYS = Path(__file__).parents[1] / "alembic" / "versions" / "9b2c3d4e5f6a_add_player_foreign_keys_to_match_events.py"


def test_reconciliation_revision_is_before_foreign_keys():
    reconciliation = MIGRATION.read_text(encoding="utf-8")
    foreign_keys = FOREIGN_KEYS.read_text(encoding="utf-8")

    assert 'down_revision = "9a1b2c3d4e5f"' in reconciliation
    assert 'down_revision = "9aa1b2c3d4e5"' in foreign_keys
    assert "provider_player_id = player_id::text" in reconciliation
    assert "provider_assist_id = assist_id::text" in reconciliation
    assert "INSERT INTO players" in reconciliation
    assert "ON CONFLICT (provider, provider_id) DO NOTHING" in reconciliation
    assert "ORDER BY count(*) DESC, player_name ASC" in reconciliation
    assert "unresolved match_events.player_id references remain" in reconciliation
    assert "unresolved match_events.assist_id references remain" in reconciliation


def test_reconciliation_handles_shared_ids_and_name_variants():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "UNION ALL" in source
    assert "player_name" in source
    assert "assist_name" in source
    assert "provider = 'api-football'" in source
    assert "GROUP BY provider_id, player_name" in source


def test_reconciliation_preserves_provider_columns_for_downgrade():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "SET player_id = events.provider_player_id::integer" in source
    assert "SET assist_id = events.provider_assist_id::integer" in source
    assert "def downgrade" in source
