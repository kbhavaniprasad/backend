"""
routers/users.py — User management endpoints for the AI Platform Auth Service.

All endpoints operate within the **caller's tenant boundary** — a user may
only see or modify users that belong to their own tenant, regardless of their
role (with the sole exception of ``super_admin`` users who may span tenants in
a future implementation).

Endpoints
---------
GET    /api/v1/users/           — List all users in the caller's tenant.
POST   /api/v1/users/           — Create a new user within the tenant.
GET    /api/v1/users/{user_id}  — Retrieve a single user by ID.
PATCH  /api/v1/users/{user_id}  — Partially update a user.
DELETE /api/v1/users/{user_id}  — Soft-deactivate a user.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import CurrentUser, require_role
from app.core.security import hash_password
from app.database import get_db
from app.models.user import RoleEnum, User
from app.schemas.auth import CreateUserRequest, UserResponse, UserUpdateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

# Roles allowed to manage users within the tenant
_MANAGER_ROLES = (RoleEnum.super_admin, RoleEnum.business_owner, RoleEnum.sales_manager)
_OWNER_ROLES = (RoleEnum.super_admin, RoleEnum.business_owner)


# --------------------------------------------------------------------------- #
# Helper                                                                       #
# --------------------------------------------------------------------------- #

async def _get_tenant_user_or_404(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> User:
    """
    Fetch a user by *user_id* that belongs to *tenant_id*.

    Raises ``HTTP 404`` if not found or belongs to a different tenant.
    """
    user: User | None = await db.scalar(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        )
    return user


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #

@router.get(
    "/",
    response_model=list[UserResponse],
    summary="List users in the caller's tenant",
    dependencies=[Depends(require_role(*_MANAGER_ROLES))],
)
async def list_users(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    skip: Annotated[int, Query(ge=0, description="Number of records to skip.")] = 0,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Maximum records to return.")
    ] = 50,
    is_active: Annotated[
        bool | None,
        Query(description="Filter by active/inactive status."),
    ] = None,
) -> list[UserResponse]:
    """
    Return a paginated list of users belonging to the caller's tenant.

    Accessible by: ``super_admin``, ``business_owner``, ``sales_manager``.

    Query parameters
    ----------------
    skip:
        Offset for pagination (default 0).
    limit:
        Page size; maximum 200 (default 50).
    is_active:
        When supplied, filters users by their ``is_active`` flag.
    """
    stmt = select(User).where(User.tenant_id == current_user.tenant_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    stmt = stmt.offset(skip).limit(limit).order_by(User.created_at.desc())

    result = await db.execute(stmt)
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


@router.post(
    "/",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user within the tenant",
    dependencies=[Depends(require_role(*_OWNER_ROLES))],
)
async def create_user(
    payload: CreateUserRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Create a new user account under the caller's tenant.

    Accessible by: ``super_admin``, ``business_owner``.

    The new user's e-mail must be globally unique (across all tenants).
    The ``business_owner`` role cannot create another ``super_admin``.
    """
    # Prevent privilege escalation
    if (
        payload.role == RoleEnum.super_admin
        and current_user.role != RoleEnum.super_admin
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super_admin users may create super_admin accounts.",
        )

    # Check e-mail uniqueness
    existing: User | None = await db.scalar(
        select(User).where(User.email == payload.email)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this e-mail address already exists.",
        )

    new_user = User(
        tenant_id=current_user.tenant_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        is_verified=False,
    )
    db.add(new_user)
    await db.flush()

    logger.info(
        "User '%s' created user '%s' (role=%s) in tenant '%s'.",
        current_user.email,
        new_user.email,
        new_user.role,
        current_user.tenant_id,
    )
    return UserResponse.model_validate(new_user)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Retrieve a single user",
)
async def get_user(
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Return the profile of a user identified by *user_id*.

    - Users may retrieve their **own** profile regardless of role.
    - Managers (``sales_manager`` and above) may retrieve any user within
      the same tenant.
    - Cross-tenant access is not permitted.
    """
    # Allow self-lookup for all roles
    if user_id != current_user.id and current_user.role not in _MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this user.",
        )

    user = await _get_tenant_user_or_404(user_id, current_user.tenant_id, db)
    return UserResponse.model_validate(user)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Partially update a user",
    dependencies=[Depends(require_role(*_OWNER_ROLES))],
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Apply a partial update to the specified user.

    Accessible by: ``super_admin``, ``business_owner``.

    - ``business_owner`` cannot promote users to ``super_admin``.
    - A user cannot deactivate or demote themselves.
    """
    user = await _get_tenant_user_or_404(user_id, current_user.tenant_id, db)

    # Prevent self-demotion / self-deactivation
    if user_id == current_user.id:
        if payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own account.",
            )
        if payload.role is not None and payload.role != current_user.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role.",
            )

    # Prevent privilege escalation
    if (
        payload.role == RoleEnum.super_admin
        and current_user.role != RoleEnum.super_admin
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only super_admin users may assign the super_admin role.",
        )

    # Apply updates
    update_data = payload.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    db.add(user)
    logger.info(
        "User '%s' updated user '%s': %s.",
        current_user.email,
        user.email,
        list(update_data.keys()),
    )
    return UserResponse.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-deactivate a user",
    dependencies=[Depends(require_role(*_OWNER_ROLES))],
)
async def deactivate_user(
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Soft-delete a user by setting ``is_active = False``.

    Accessible by: ``super_admin``, ``business_owner``.

    The user record is **not** permanently deleted so that audit trails and
    historical data remain intact.  A deactivated user cannot log in.
    """
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    user = await _get_tenant_user_or_404(user_id, current_user.tenant_id, db)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already deactivated.",
        )

    user.is_active = False
    db.add(user)

    logger.info(
        "User '%s' deactivated user '%s' in tenant '%s'.",
        current_user.email,
        user.email,
        current_user.tenant_id,
    )
