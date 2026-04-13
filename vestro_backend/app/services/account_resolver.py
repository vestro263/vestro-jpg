from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AccountResolver:

    @staticmethod
    async def resolve_by_email(db: AsyncSession, email: str):
        result = await db.execute(text("""
            SELECT
                c.account_id,
                c.is_demo,
                c.is_active,
                u.email,
                u.id as user_id
            FROM users u
            JOIN credentials c ON c.user_id = u.id
            WHERE LOWER(u.email) = LOWER(:email)
              AND c.is_active = true
        """), {"email": email})

        return result.fetchall()


    @staticmethod
    async def resolve_by_user_id(db: AsyncSession, user_id: str):
        result = await db.execute(text("""
            SELECT account_id, is_demo, is_active
            FROM credentials
            WHERE user_id = :user_id
              AND is_active = true
        """), {"user_id": user_id})

        return result.fetchall()