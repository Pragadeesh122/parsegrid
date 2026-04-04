"""ParseGrid API — Auth endpoints for Next.js 16 integration."""

import uuid

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.user import User
from app.schemas.user import (
    CredentialVerifyRequest,
    OAuthUpsertRequest,
    UserCreateRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Auth"])


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


@router.post(
    "/verify-credentials",
    response_model=UserResponse,
    summary="Internal endpoint for Next.js CredentialsProvider",
)
async def verify_credentials(
    body: CredentialVerifyRequest,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Verifies a user's plain-text password and returns the User object context.
    
    This is called exclusively by NextAuth's `authorize()` function.
    """
    query = select(User).where(User.email == body.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    if not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    return user


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new internal developer user",
)
async def register_user(
    body: UserCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Registers a new user in the Community Edition."""
    query = select(User).where(User.email == body.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        id=str(uuid.uuid4()),
        email=body.email,
        name=body.name,
        hashed_password=get_password_hash(body.password),
        auth_provider="credentials",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/oauth-upsert",
    response_model=UserResponse,
    summary="Create or find an OAuth user (called by Auth.js signIn callback)",
)
async def oauth_upsert(
    body: OAuthUpsertRequest,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Finds an existing user by email or creates a new one for OAuth sign-ins.

    Called by Auth.js signIn callback when a user authenticates via GitHub/Google.
    Next.js NEVER touches the database — this endpoint handles the upsert.
    """
    query = select(User).where(User.email == body.email)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if user:
        # Update name if it was missing
        if body.name and not user.name:
            user.name = body.name
            await db.commit()
            await db.refresh(user)
        return user

    # Create new OAuth user (no password)
    user = User(
        id=str(uuid.uuid4()),
        email=body.email,
        name=body.name,
        hashed_password=None,
        auth_provider=body.auth_provider,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user

