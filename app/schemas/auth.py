from pydantic import BaseModel


class GoogleLoginRequest(BaseModel):
    token_in: str
