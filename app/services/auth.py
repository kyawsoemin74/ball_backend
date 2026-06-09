import secrets

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from sqlalchemy import or_, select

from app.core.config import settings
from app.models.user import User
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
        # Temporarily force admin role for all new registrations
        role = "admin"

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

    async def authenticate_google_user(self, db: AsyncSession, email: str, google_id: str, username: str) -> User:
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
                # 3. Create new user
                # Generate a unique random password for Google-authenticated users.
                random_password = secrets.token_urlsafe(32)

                user = User(
                    username=username,
                    email=email,
                    google_id=google_id,
                    hashed_password=self.hash_password(random_password),
                    role="user",
                    is_active=True
                )
                db.add(user)
            
            try:
                await db.commit()
                await db.refresh(user)
            except Exception as e:
                await db.rollback()
                print("GOOGLE DB ERROR:", repr(e))
                raise HTTPException(
                    status_code=400,
                    detail=str(e)
                )

        return user

    def create_token_pair(self, user: User) -> dict:
        return {
            "access_token": self.token_service.create_access_token(user.username, user.role),
            "refresh_token": self.token_service.create_refresh_token(user.username, user.role),
            "token_type": "bearer",
        }


auth_service = AuthService()
