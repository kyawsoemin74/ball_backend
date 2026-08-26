import pytest

from ops.backup import role_verify


class Result:
    def __init__(self, stdout):
        self.stdout = stdout


def test_missing_role_is_detected(monkeypatch):
    monkeypatch.setattr(role_verify.subprocess, "run", lambda *args, **kwargs: Result(""))
    with pytest.raises(RuntimeError, match="role is missing"):
        role_verify.validate_role_grants("postgresql://disposable/db", "fover_user")


def test_missing_grant_is_detected(monkeypatch):
    outputs = iter(("fover_user|t|f\n", "t|t|f|t|t\n"))
    monkeypatch.setattr(role_verify.subprocess, "run", lambda *args, **kwargs: Result(next(outputs)))
    with pytest.raises(RuntimeError, match="grant is missing"):
        role_verify.validate_role_grants("postgresql://disposable/db", "fover_user", ("news", "matches"))


def test_valid_role_and_grants_pass(monkeypatch):
    outputs = iter(("fover_user|t|f\n", "t|t|t|t\n"))
    monkeypatch.setattr(role_verify.subprocess, "run", lambda *args, **kwargs: Result(next(outputs)))
    role_verify.validate_role_grants("postgresql://disposable/db", "fover_user", ("news",))