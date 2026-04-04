"""ParseGrid API — User and Authentication schemas."""

from pydantic import BaseModel, ConfigDict, EmailStr


class UserCreateRequest(BaseModel):
    """Schema for registering a new user."""

    email: EmailStr
    name: str | None = None
    password: str


class CredentialVerifyRequest(BaseModel):
    """Schema for NextAuth backend validation."""

    email: EmailStr
    password: str


class OAuthUpsertRequest(BaseModel):
    """Schema for OAuth user creation/lookup.
    Called by Auth.js signIn callback for GitHub/Google users."""

    email: EmailStr
    name: str | None = None
    auth_provider: str  # "github" or "google"


class UserResponse(BaseModel):
    """Schema for public user object."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str | None

