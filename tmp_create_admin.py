import asyncio
from sqlalchemy import text
from app.db import async_session
from app.services.auth import auth_service
from app.schemas.user import UserCreate

async def main():
    async with async_session() as db:
        try:
            await db.execute(text('SELECT 1'))
            print('db-ok')
        except Exception as exc:
            print('db-error', exc)
            return
        user_in = UserCreate(username='liveverify_admin', email='liveverify_admin@example.com', password='Password123!')
        try:
            user = await auth_service.register_user(db, user_in)
            print('created', user.username, user.role, user.is_active)
        except Exception as exc:
            print('create-error', repr(exc))

asyncio.run(main())
