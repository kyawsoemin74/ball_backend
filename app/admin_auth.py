from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.responses import RedirectResponse
from app.services.auth import auth_service
from app.services.token import TokenService
from app.db import async_session
from app.core.config import settings

class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        async with async_session() as db:
            try:
                # Authenticate using existing service
                user = await auth_service.authenticate_user(
                    db=db, 
                    username=username, 
                    password=password
                )
                
                # Verify admin role
                if user.role != "admin":
                    return False

                # Create token and set session
                token_pair = auth_service.create_token_pair(user)
                request.session.update({"token": token_pair["access_token"]})
                return True
            except Exception:
                return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("token")
        if not token:
            return False

        token_service = TokenService()
        try:
            payload = token_service.decode_token(token, expected_type="access")
            return payload.get("role") == "admin"
        except Exception:
            return False

authentication_backend = AdminAuth(secret_key=settings.JWT_SECRET_KEY)