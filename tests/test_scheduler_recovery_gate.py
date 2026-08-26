def test_scheduler_recovery_gate_defaults_to_enabled(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("FOOTBALL_API_KEY", "disposable-provider-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "disposable-jwt-key")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "disposable-google-client")
    assert Settings(_env_file=None).SCHEDULER_ENABLED is True


def test_scheduler_recovery_gate_can_be_disabled(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("FOOTBALL_API_KEY", "disposable-provider-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "disposable-jwt-key")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "disposable-google-client")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    assert Settings(_env_file=None).SCHEDULER_ENABLED is False