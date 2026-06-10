from pydantic import BaseModel, EmailStr


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class GoogleAuthUser(BaseModel):
    id: str
    email: EmailStr
    name: str | None = None
    avatarUrl: str | None = None
    provider: str = "google"


class GoogleAuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: GoogleAuthUser


class TokenPayload(BaseModel):
    sub: str
    role: str
    exp: int
    type: str
