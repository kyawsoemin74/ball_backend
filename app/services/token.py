from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import HTTPException, status
from jose import JWTError, jwt

from app.core.config import settings
from app.monitoring import JWT_FAILURES


class TokenService:
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.access_token_expire_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_token_expire_minutes = settings.REFRESH_TOKEN_EXPIRE_MINUTES

    def _create_token(self, subject: str, role: str, expires_delta: timedelta, token_type: str) -> str:
        expire = datetime.utcnow() + expires_delta
        payload: Dict[str, Any] = {
            "sub": subject,
            "role": role,
            "type": token_type,
            "exp": expire,
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_access_token(self, subject: str, role: str) -> str:
        return self._create_token(
            subject=subject,
            role=role,
            expires_delta=timedelta(minutes=self.access_token_expire_minutes),
            token_type="access",
        )

    def create_refresh_token(self, subject: str, role: str) -> str:
        return self._create_token(
            subject=subject,
            role=role,
            expires_delta=timedelta(minutes=self.refresh_token_expire_minutes),
            token_type="refresh",
        )

    def decode_token(self, token: str, expected_type: str = "access") -> Dict[str, Any]:
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            if payload.get("type") != expected_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if payload.get("sub") is None or payload.get("role") is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return payload
        except JWTError:
            JWT_FAILURES.inc()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
