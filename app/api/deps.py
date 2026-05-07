"""
Dependencies for API routes.
"""
from app.core.security import get_api_key

# Re-export the API key security dependency for easier import in routes
api_key_security = get_api_key