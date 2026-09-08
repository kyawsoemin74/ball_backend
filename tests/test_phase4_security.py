import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.api.deps import current_active_admin
from app.core.config import Settings, settings
from app.core.security import get_current_active_admin
from app.db import get_db
from app.main import app
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.auth import AuthService
from app.services.token import TokenService


class EmptyResult:
    def scalar_one_or_none(self):
        return None


class RegistrationDB:
    def __init__(self):
        self.user = None

    async def execute(self, query):
        return EmptyResult()

    def add(self, user):
        self.user = user

    async def commit(self):
        return None

    async def refresh(self, user):
        user.id = 1


def test_public_registration_cannot_create_admin():
    db = RegistrationDB()
    user = asyncio.run(
        AuthService().register_user(
            db,
            UserCreate(
                username="security-user",
                email="security@example.com",
                password="strong-password",
                role="admin",
            ),
        )
    )

    assert user.role == "user"
    assert db.user.role == "user"


def test_upload_requires_admin_authentication():
    client = TestClient(app)
    response = client.post(
        "/api/uploads/news",
        files={"file": ("image.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 401


def test_invalid_and_expired_tokens_are_rejected():
    token_service = TokenService()
    with pytest.raises(Exception):
        token_service.decode_token("not-a-token")

    expired = jwt.encode(
        {
            "sub": "expired-user",
            "role": "user",
            "type": "access",
            "exp": datetime.utcnow() - timedelta(minutes=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(Exception):
        token_service.decode_token(expired)


def test_production_rejects_weak_jwt_secret():
    production = Settings(
        _env_file=None,
        APP_ENV="production",
        FOOTBALL_API_KEY="provider-key",
        JWT_SECRET_KEY="short",
        GOOGLE_CLIENT_ID="google-client",
    )
    with pytest.raises(ValueError):
        production.validate_production_secrets()


def test_production_policy_disables_public_docs_and_metrics():
    production = Settings(
        _env_file=None,
        APP_ENV="production",
        ENABLE_API_DOCS=False,
        ENABLE_API_METRICS=False,
        FOOTBALL_API_KEY="provider-key",
        JWT_SECRET_KEY="x" * 32,
        GOOGLE_CLIENT_ID="google-client",
    )
    assert production.ENABLE_API_DOCS is False
    assert production.ENABLE_API_METRICS is False


def test_admin_dependency_rejects_normal_user():
    normal_user = SimpleNamespace(role="user", is_active=True)
    with pytest.raises(Exception):
        get_current_active_admin(normal_user)
