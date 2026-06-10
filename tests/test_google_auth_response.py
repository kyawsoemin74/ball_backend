from types import SimpleNamespace

from app.services.auth import AuthService


def test_google_auth_response_includes_user_profile():
    auth_service = AuthService()
    user = SimpleNamespace(
        id=42,
        username="google-user",
        email="google-user@example.com",
        role="user",
    )

    response = auth_service.create_google_auth_response(user)

    assert response.access_token
    assert response.refresh_token
    assert response.token_type == "bearer"
    assert response.user.id == "42"
    assert response.user.email == "google-user@example.com"
    assert response.user.name == "google-user"
    assert response.user.avatarUrl is None
    assert response.user.provider == "google"
