import secrets
import string

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from sqlalchemy import or_, select

from app.core.config import settings
from app.models.user import AvatarSource, User
from app.schemas.token import GoogleAuthResponse, GoogleAuthUser
from app.schemas.user import UserCreate
from app.services.token import TokenService


class AuthService:
    ALLOWED_ROLES = {"admin", "premium", "user"}

    def __init__(self):
        self.token_service = TokenService()

    def hash_password(self, password: str) -> str:
        # bcrypt requires bytes, so we encode the password string
        pwd_bytes = password.encode("utf-8")
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(pwd_bytes, salt)
        return hashed.decode("utf-8")

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), 
            hashed_password.encode("utf-8")
        )

    async def register_user(self, db: AsyncSession, user_in: UserCreate) -> User:
        role = "user"

        result = await db.execute(
            select(User).where(
                or_(User.username == user_in.username, User.email == user_in.email)
            )
        )
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with that username or email already exists.",
            )

        user = User(
            username=user_in.username,
            email=user_in.email,
            hashed_password=self.hash_password(user_in.password),
            role=role,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    async def authenticate_user(self, db: AsyncSession, username: str, password: str) -> User:
        result = await db.execute(
            select(User).where(
                or_(User.username == username, User.email == username)
            )
        )
        user = result.scalar_one_or_none()
        if not user or not self.verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    def _sync_google_profile(self, user: User, username: str | None, picture: str | None) -> None:
        if user.avatar_source == AvatarSource.UPLOAD:
            if username:
                user.display_name = username
            return

        if user.avatar_source == AvatarSource.GOOGLE:
            if username:
                user.display_name = username
            if picture:
                user.avatar_url = picture
            return

        if username:
            user.display_name = username
        if picture:
            user.avatar_url = picture
        user.avatar_source = AvatarSource.GOOGLE

    async def authenticate_google_user(
        self,
        db: AsyncSession,
        email: str,
        google_id: str,
        username: str,
        picture: str | None = None,
    ) -> User:
        # 1. Check if user exists by google_id
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()

        if not user:
            # 2. If not, check if email already exists (link account)
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

            if user:
                # Update existing user with google_id
                user.google_id = google_id
            else:
                # 3. Create new user using the email-derived username and ensure it stays unique.
                base_username = email.split("@")[0]
                candidate_username = base_username

                result = await db.execute(select(User).where(User.username == candidate_username))
                existing_user = result.scalar_one_or_none()

                while existing_user:
                    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(6))
                    candidate_username = f"{base_username}_{suffix}"
                    result = await db.execute(select(User).where(User.username == candidate_username))
                    existing_user = result.scalar_one_or_none()

                # Generate a unique random password for Google-authenticated users.
                random_password = secrets.token_urlsafe(32)

                user = User(
                    username=candidate_username,
                    email=email,
                    google_id=google_id,
                    hashed_password=self.hash_password(random_password),
                    role="user",
                    is_active=True,
                    display_name=username,
                    avatar_url=picture,
                    avatar_source=AvatarSource.GOOGLE if picture else AvatarSource.DEFAULT,
                )
                db.add(user)

        if user:
            self._sync_google_profile(user, username, picture)
            try:
                await db.commit()
                await db.refresh(user)
            except Exception as e:
                await db.rollback()
                print("GOOGLE DB ERROR:", repr(e))
                raise HTTPException(
                    status_code=400,
                    detail="Could not create user from Google account"
                )

        return user

    def create_token_pair(self, user: User) -> dict:
        return {
            "access_token": self.token_service.create_access_token(user.username, user.role),
            "refresh_token": self.token_service.create_refresh_token(user.username, user.role),
            "token_type": "bearer",
        }

    def create_google_auth_response(self, user: User) -> GoogleAuthResponse:
        token_pair = self.create_token_pair(user)

        return GoogleAuthResponse(
            access_token=token_pair["access_token"],
            refresh_token=token_pair["refresh_token"],
            token_type=token_pair["token_type"],
            user=GoogleAuthUser(
                id=str(user.id),
                email=user.email,
                name=user.username,
                avatarUrl=user.avatar_url,
                provider="google",
            ),
        )


auth_service = AuthService()
