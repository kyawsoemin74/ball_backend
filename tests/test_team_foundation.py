from pathlib import Path


def test_team_master_model_and_migration_exist():
    team_model = Path("app/models/team.py")
    assert team_model.exists(), "Team model file is missing"

    model_text = team_model.read_text(encoding="utf-8")
    assert "country_id" in model_text
    assert "provider" in model_text
    assert "provider_id" in model_text
    assert "ForeignKey(\"countries.country_id\"" in model_text

    migration_files = list(Path("alembic/versions").glob("*team*.py"))
    assert migration_files, "Team master migration is missing"

    migration_text = "\n".join(path.read_text(encoding="utf-8") for path in migration_files)
    assert "teams" in migration_text
    assert "country_id" in migration_text
    assert "fk_teams_country_id" in migration_text or "countries" in migration_text
    assert "provider" in migration_text or "provider_id" in migration_text
