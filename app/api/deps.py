"""
Dependencies for API routes.
"""
from app.core.security import get_current_active_admin, get_current_active_user

current_active_user = get_current_active_user
current_active_admin = get_current_active_admin
api_key_security = get_current_active_user