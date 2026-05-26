from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserRead
from app.schemas.token import Token
from app.services.auth import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(user_in: UserCreate, db: AsyncSession = Depends(get_db)):
    """Register a new user account and return the created user details."""
    user = await auth_service.register_user(db=db, user_in=user_in)
    return user


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate a user and return access and refresh tokens."""
    user = await auth_service.authenticate_user(
        db=db,
        username=form_data.username,
        password=form_data.password,
    )
    return auth_service.create_token_pair(user)


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access token."""
    payload = auth_service.token_service.decode_token(refresh_token, expected_type="refresh")
    username = payload["sub"]
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth_service.create_token_pair(user)

@router.post("/google", response_model=Token)
async def google_login(token_in: str, db: AsyncSession = Depends(get_db)):
    """Authenticate user using Google ID Token."""
    try:
        # 1. Verify token with Google
        # token_in သည် frontend (Flutter/React) မှ ပေးပို့လိုက်သော ID Token ဖြစ်ရပါမည်။
        idinfo = id_token.verify_oauth2_token(
            token_in, 
            google_requests.Request(), 
            settings.GOOGLE_CLIENT_ID
        )

        # 2. Get email, google_id, and name from payload
        email = idinfo['email']
        google_id = idinfo['sub']
        name = idinfo.get('name', email.split('@')[0])  # name မရှိရင် email ရှေ့ပိုင်းကို ယူမယ်

    except ValueError:
        # Invalid token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        )

    user = await auth_service.authenticate_google_user(
        db=db,
        email=email,
        google_id=google_id,
        username=name
    )
    return auth_service.create_token_pair(user)
