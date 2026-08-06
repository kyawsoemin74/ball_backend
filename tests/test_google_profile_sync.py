from types import SimpleNamespace

from app.models.user import AvatarSource, User
from app.services.auth import AuthService


def test_google_auth_response_uses_persisted_avatar_url():
    auth_service = AuthService()
    user = SimpleNamespace(
        id=42,
        username="google-user",
        email="google-user@example.com",
        role="user",
        avatar_url="https://example.com/avatar.jpg",
    )

    response = auth_service.create_google_auth_response(user)

    assert response.user.avatarUrl == "https://example.com/avatar.jpg"


def test_avatar_source_enum_values_are_supported():
    assert AvatarSource.DEFAULT.value == "default"
    assert AvatarSource.GOOGLE.value == "google"
    assert AvatarSource.UPLOAD.value == "upload"


def test_sync_google_profile_updates_existing_user_fields():
    auth_service = AuthService()
    user = User(
        username="existing-user",
        email="existing@example.com",
        hashed_password="hashed",
        role="user",
        is_active=True,
        avatar_source=AvatarSource.DEFAULT,
    )

    auth_service._sync_google_profile(user, "Existing User", "https://example.com/avatar.jpg")

    assert user.display_name == "Existing User"
    assert user.avatar_url == "https://example.com/avatar.jpg"
    assert user.avatar_source == AvatarSource.GOOGLE
