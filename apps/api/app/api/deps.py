"""ParseGrid API — Shared FastAPI dependencies."""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import TokenPayload, verify_jwt


async def get_current_user(
    token: TokenPayload = Depends(verify_jwt),
) -> TokenPayload:
    """Returns the authenticated user from the JWT.
    Use as a dependency in protected endpoints.
    """
    return token


async def get_db_session(
    session: AsyncSession = Depends(get_db),
) -> AsyncSession:
    """Alias for get_db — enables cleaner dependency chains."""
    return session
