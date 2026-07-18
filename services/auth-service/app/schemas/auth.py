"""
schemas/auth.py — Pydantic request / response schemas for the Auth Service.

All schemas use strict types and validators so that invalid payloads are
rejected at the boundary before touching the database.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import PlanEnum, RoleEnum


# --------------------------------------------------------------------------- #
# Request schemas                                                              #
# --------------------------------------------------------------------------- #

class RegisterRequest(BaseModel):
    """Payload for new tenant + owner registration."""

    email: EmailStr = Field(..., description="User's e-mail address.")
    password: str = Field(
        ...,
        min_length=8,
        description="Plain-text password; minimum 8 characters.",
    )
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User's display name.",
    )
    tenant_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable organisation name.",
    )
    tenant_slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description=(
            "URL-safe slug for the tenant, e.g. 'acme-corp'. "
            "Lowercase letters, digits, and hyphens only."
        ),
    )

    @field_validator("password")
    @classmethod
    def password_complexity(cls, value: str) -> str:
        """Ensure the password contains at least one digit and one letter."""
        has_letter = any(c.isalpha() for c in value)
        has_digit = any(c.isdigit() for c in value)
        if not (has_letter and has_digit):
            raise ValueError(
                "Password must contain at least one letter and one digit."
            )
        return value


class LoginRequest(BaseModel):
    """Credentials for the password-based login flow."""

    email: EmailStr = Field(..., description="Registered e-mail address.")
    password: str = Field(..., description="Plain-text password.")


class RefreshRequest(BaseModel):
    """Payload for exchanging a refresh token for a new access token."""

    refresh_token: str = Field(..., description="A valid, unexpired refresh token.")


class GoogleOAuthRequest(BaseModel):
    """Payload delivered by the front-end after a Google OAuth2 callback."""

    code: str = Field(..., description="Authorization code returned by Google.")
    redirect_uri: str = Field(
        ..., description="Must match the redirect_uri registered in Google Console."
    )


# --------------------------------------------------------------------------- #
# Response schemas                                                             #
# --------------------------------------------------------------------------- #

class TokenResponse(BaseModel):
    """Token pair returned after a successful authentication."""

    access_token: str = Field(..., description="Short-lived JWT access token.")
    refresh_token: str = Field(..., description="Long-lived JWT refresh token.")
    token_type: str = Field(default="bearer", description="OAuth2 token type.")
    expires_in: int = Field(
        ...,
        description="Access token lifetime in seconds.",
    )


class UserResponse(BaseModel):
    """Safe public representation of a User record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: RoleEnum
    tenant_id: uuid.UUID
    is_active: bool
    is_verified: bool
    created_at: datetime


class TenantResponse(BaseModel):
    """Public representation of a Tenant record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    plan: PlanEnum
    is_active: bool
    created_at: datetime


class UserWithTenantResponse(UserResponse):
    """Extended user response that also embeds tenant information."""

    tenant: TenantResponse


# --------------------------------------------------------------------------- #
# Update schemas                                                               #
# --------------------------------------------------------------------------- #

class UserUpdateRequest(BaseModel):
    """
    Partial update payload for a user record.

    All fields are optional — only provided fields will be updated.
    """

    full_name: str | None = Field(default=None, max_length=255)
    role: RoleEnum | None = Field(default=None)
    is_active: bool | None = Field(default=None)

    model_config = ConfigDict(extra="forbid")


class CreateUserRequest(BaseModel):
    """Admin-initiated user creation within an existing tenant."""

    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=1, max_length=255)
    role: RoleEnum = Field(default=RoleEnum.agent_viewer)
