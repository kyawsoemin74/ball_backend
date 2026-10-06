from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import get_current_user, oauth2_scheme
from app.db import get_db
from app.models.auth_session import AuthSession
from app.models.user import User
from app.schemas.auth import GoogleLoginRequest
from app.schemas.user import UserCreate, UserRead
from app.schemas.token import GoogleAuthResponse, LogoutResponse, Token
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
    session = await auth_service.create_auth_session(db=db, user=user)
    token_pair = auth_service.create_token_pair(user, sid=session.sid)
    session.refresh_token_identity = auth_service.hash_refresh_token(token_pair["refresh_token"])
    await db.commit()
    return token_pair


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str, db: AsyncSession = Depends(get_db)):
    """Exchange a refresh token for a new access token."""
    payload = auth_service.token_service.decode_token(refresh_token, expected_type="refresh")
    sid = payload.get("sid")
    if not sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = payload["sub"]
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session = await auth_service.get_session_by_sid(db, sid)
    if not session or session.user_id != user.id or session.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if session.refresh_token_identity != auth_service.hash_refresh_token(refresh_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_pair = auth_service.create_token_pair(user, sid=sid)
    session.refresh_token_identity = auth_service.hash_refresh_token(token_pair["refresh_token"])
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES)
    await db.commit()
    return token_pair


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke the current session without invalidating other sessions."""
    payload = auth_service.token_service.decode_token(token, expected_type="access")
    sid = payload.get("sid")
    if not sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session = await auth_service.get_session_by_sid(db, sid)
    if session and session.user_id == current_user.id:
        if session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            await db.commit()
    return auth_service.create_logout_response()


@router.post("/google", response_model=GoogleAuthResponse)
async def google_login(
    request: GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user using Google ID Token."""
    token_in = request.token_in

    try:
        # 1. Verify token with Google
        # token_in သည် frontend (Flutter/React) မှ ပေးပို့လိုက်သော ID Token ဖြစ်ရပါမည်။
        idinfo = id_token.verify_oauth2_token(
            token_in,
            google_requests.Request(),
            settings.GOOGLE_CLIENT_ID,
        )

        # 2. Get email, google_id, name, and picture from payload
        email = idinfo['email']
        google_id = idinfo['sub']
        name = idinfo.get('name', email.split('@')[0])  # name မရှိရင် email ရှေ့ပိုင်းကို ယူမယ်
        picture = idinfo.get('picture')

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
        username=name,
        picture=picture,
    )
    session = await auth_service.create_auth_session(db=db, user=user)
    token_pair = auth_service.create_token_pair(user, sid=session.sid)
    session.refresh_token_identity = auth_service.hash_refresh_token(token_pair["refresh_token"])
    await db.commit()
    return auth_service.create_google_auth_response(user, sid=session.sid)
