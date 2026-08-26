import os
import subprocess
import sys


def test_missing_required_configuration_fails_without_secret_values():
    environment = os.environ.copy()
    sentinel = "DISPOSABLE_SECRET_SENTINEL"
    for name in ("FOOTBALL_API_KEY", "JWT_SECRET_KEY", "GOOGLE_CLIENT_ID"):
        environment.pop(name, None)
    environment["UNRELATED_SECRET_VALUE"] = sentinel
    result = subprocess.run(
        [sys.executable, "-c", "from app.core.config import Settings; Settings(_env_file=None)"],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "FOOTBALL_API_KEY" in result.stderr
    assert "JWT_SECRET_KEY" in result.stderr
    assert "GOOGLE_CLIENT_ID" in result.stderr
    assert sentinel not in result.stderr