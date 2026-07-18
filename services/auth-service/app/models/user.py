"""
models/user.py — ORM models for Tenant, User, and RefreshToken.

All primary keys use server-generated UUIDs so that IDs are globally unique
and do not leak row-count information.  Timestamps are always stored in UTC.
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


# --------------------------------------------------------------------------- #
# Enumerations                                                                 #
# --------------------------------------------------------------------------- #

class PlanEnum(str, enum.Enum):
    """Billing / feature-gating plans available for a tenant."""

    free = "free"
    starter = "starter"
    professional = "professional"
    enterprise = "enterprise"


class RoleEnum(str, enum.Enum):
    """
    User roles within the platform.

    Hierarchy (highest → lowest privilege):
    super_admin > business_owner > sales_manager > agent_viewer
    """

    super_admin = "super_admin"
    business_owner = "business_owner"
    sales_manager = "sales_manager"
    agent_viewer = "agent_viewer"


# --------------------------------------------------------------------------- #
# Helper                                                                       #
# --------------------------------------------------------------------------- #

def _utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(tz=timezone.utc)


# --------------------------------------------------------------------------- #
# Tenant                                                                       #
# --------------------------------------------------------------------------- #

class Tenant(Base):
    """
    Represents an organisational tenant (customer account).

    Each tenant is isolated: users, leads, and campaigns belong to exactly
    one tenant.  The ``slug`` acts as a human-readable, URL-safe identifier.
    """

    __tablename__ = "tenants"

    id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
        comment="Globally unique tenant identifier.",
    )
    name: str = Column(
        String(255),
        nullable=False,
        comment="Human-readable display name of the organisation.",
    )
    slug: str = Column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
        comment="URL-safe unique slug, e.g. 'acme-corp'.",
    )
    plan: PlanEnum = Column(
        Enum(PlanEnum, name="plan_enum"),
        nullable=False,
        default=PlanEnum.free,
        comment="Current billing / feature plan.",
    )
    is_active: bool = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Set to False to soft-disable the tenant.",
    )
    created_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        comment="Record creation timestamp (UTC).",
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        comment="Last modification timestamp (UTC).",
    )

    # Relationships
    users: list["User"] = relationship(
        "User",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant id={self.id} slug={self.slug!r}>"


# --------------------------------------------------------------------------- #
# User                                                                         #
# --------------------------------------------------------------------------- #

class User(Base):
    """
    Platform user belonging to a single tenant.

    Passwords are stored as bcrypt hashes via ``app.core.security``.
    The ``is_verified`` flag is set to True after e-mail verification.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
        comment="Globally unique user identifier.",
    )
    tenant_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owning tenant.",
    )
    email: str = Column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
        comment="User's e-mail address (globally unique across all tenants).",
    )
    hashed_password: str = Column(
        Text,
        nullable=True,  # Nullable for OAuth-only accounts
        comment="bcrypt hash of the user's password.",
    )
    full_name: str = Column(
        String(255),
        nullable=False,
        default="",
        comment="Display name of the user.",
    )
    role: RoleEnum = Column(
        Enum(RoleEnum, name="role_enum"),
        nullable=False,
        default=RoleEnum.agent_viewer,
        comment="User role controlling access privileges.",
    )
    is_active: bool = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Soft-delete / disable flag.",
    )
    is_verified: bool = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="True after the user verifies their e-mail address.",
    )
    created_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    last_login: datetime | None = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the user's most recent successful login.",
    )

    # Relationships
    tenant: "Tenant" = relationship("Tenant", back_populates="users", lazy="noload")
    refresh_tokens: list["RefreshToken"] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


# --------------------------------------------------------------------------- #
# RefreshToken                                                                 #
# --------------------------------------------------------------------------- #

class RefreshToken(Base):
    """
    Persisted refresh-token record.

    Storing refresh tokens in the database allows us to implement
    token rotation and single-use invalidation without shared state.
    """

    __tablename__ = "refresh_tokens"

    id: uuid.UUID = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    user_id: uuid.UUID = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: str = Column(
        Text,
        nullable=False,
        unique=True,
        index=True,
        comment="Opaque signed JWT refresh token (unique per issuance).",
    )
    expires_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="Absolute expiry time; reject the token after this point.",
    )
    created_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    # Relationships
    user: "User" = relationship("User", back_populates="refresh_tokens", lazy="noload")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RefreshToken id={self.id} user_id={self.user_id}>"
