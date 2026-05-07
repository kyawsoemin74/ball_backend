from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

# Define the API key header. The client must send 'X-API-KEY' in the header.
# auto_error=True means FastAPI will automatically raise a 403 if the header is missing.
api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=True)

async def get_api_key(api_key: str = Security(api_key_header)):
    """
    Dependency function to validate the API key provided in the request header.
    If the API key is missing or incorrect, it raises a 403 Forbidden exception.
    """
    # Compare the provided API key with the one configured in settings
    if api_key == settings.API_KEY:
        return api_key
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )