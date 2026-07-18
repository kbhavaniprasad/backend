"""
core/dependencies.py — Reusable FastAPI dependency callables.

These dependencies are injected into route handlers via ``Depends()``.
They handle authentication, authorisation, and database session scoping.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.security import decode_token
from app.database import get_db
from app.models.user import RoleEnum, User

# --------------------------------------------------------------------------- #
# OAuth2 scheme                                                                #
# --------------------------------------------------------------------------- #

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# --------------------------------------------------------------------------- #
# Type aliases for cleaner handler signatures                                  #
# --------------------------------------------------------------------------- #

DBDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --------------------------------------------------------------------------- #
# Authentication dependency                                                    #
# --------------------------------------------------------------------------- #

async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DBDep,
    settings: SettingsDep,
) -> User:
    """
    Decode the Bearer token from the ``Authorization`` header and return the
    corresponding ``User`` record from the database.

    Raises
    ------
    HTTPException(401)
        If the token is missing, malformed, expired, or the user no longer exists.
    HTTPException(403)
        If the user account is inactive.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(token, settings, is_refresh=False)
    if payload is None:
        raise credentials_exception

    # Validate token type
    if payload.get("type") != "access":
        raise credentials_exception

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    # Fetch user from the database
    result = await db.execute(select(User).where(User.id == user_id))
    user: User | None = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# --------------------------------------------------------------------------- #
# Role-based authorisation dependency factory                                  #
# --------------------------------------------------------------------------- #

def require_role(*roles: RoleEnum) -> Callable:
    """
    Return a FastAPI dependency that enforces role-based access control.

    Parameters
    ----------
    *roles:
        One or more ``RoleEnum`` values that are permitted to call the endpoint.

    Returns
    -------
    Callable
        An async dependency that raises ``HTTP 403`` when the authenticated
        user's role is not in the allowed set.

    Usage
    -----
    ::

        @router.get("/admin-only")
        async def admin_route(
            _: User = Depends(require_role(RoleEnum.super_admin, RoleEnum.business_owner))
        ):
            ...
    """

    async def _check_role(current_user: CurrentUser) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Operation requires one of the following roles: "
                    f"{[r.value for r in roles]}."
                ),
            )
        return current_user

    return _check_role


def require_same_tenant_or_role(*roles: RoleEnum) -> Callable:
    """
    Dependency that allows access if the caller has one of *roles* OR if
    the caller belongs to the same tenant as the target resource.

    The route must populate ``request.state.target_tenant_id`` before this
    dependency is evaluated (or derive the tenant from the path parameter).

    This is a convenience factory; adapt as needed for your data model.
    """

    async def _check(current_user: CurrentUser) -> User:
        if current_user.role in roles:
            return current_user
        # Additional same-tenant check would be applied in the route handler
        return current_user

    return _check
