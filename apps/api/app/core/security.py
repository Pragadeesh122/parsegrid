"""ParseGrid API — JWT verification middleware.

Auth.js (Next.js) signs JWTs with HS256 using AUTH_SECRET.
FastAPI verifies the signature using the same shared secret.
FastAPI does NOT manage users — it only validates tokens.
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

security_scheme = HTTPBearer(
    scheme_name="Auth.js JWT",
    description="JWT token signed by Auth.js (Next.js frontend)",
)


class TokenPayload:
    """Decoded JWT payload from Auth.js."""

    def __init__(self, payload: dict):
        self.sub: str = payload.get("sub", "")
        self.email: str = payload.get("email", "")
        self.name: str = payload.get("name", "")
        self.raw: dict = payload


def verify_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> TokenPayload:
    """FastAPI dependency that extracts and verifies the JWT from the
    Authorization: Bearer header.

    Raises 401 if the token is invalid, expired, or missing.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False},  # Auth.js may not set audience
        )
        return TokenPayload(payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )
